"""Bounded native replay ownership and durable progress; never uses TC routes."""
import threading
from .evidence import DiscoveryError
from .replay import execute


class ReplayRegistry:
    def __init__(self):
        self.lock=threading.Lock();self.workers={}

    def start(self,repository,identity,owner,configuration,version=None):
        with self.lock:
            active=[value for value in self.workers.values() if value['thread'].is_alive()]
            if len(active)>=2 or any(value['owner']==owner for value in active):
                raise DiscoveryError('Replay capacity is busy; finish or cancel the current execution.')
            run_id,collection=repository.new_run(identity,owner,version)
            cancel=threading.Event()
            def work():
                completed=[]
                try:
                    def progress(result):
                        completed.append(result)
                        total=len(configuration.get('selected_request_ids') or collection['document']['requests'])
                        repository.update_run(run_id,owner,{'status':'RUNNING','completed':len(completed),'total':total,'results':completed})
                    result=execute(collection['document'],configuration,cancel,progress=progress)
                    repository.update_run(run_id,owner,result,final=True)
                except Exception:
                    # Never log request/config/credential values or hide failure
                    # behind PASS. Keep completed sanitized evidence available.
                    repository.update_run(run_id,owner,{'status':'ERROR','results':completed,'reason':'Execution failed; no historical artifacts were overwritten.'})
            thread=threading.Thread(target=work,name='api-discovery-replay-'+run_id[:8],daemon=True)
            self.workers={key:value for key,value in self.workers.items() if value['thread'].is_alive()}
            self.workers[run_id]={'owner':owner,'thread':thread,'cancel':cancel}
            thread.start();return run_id

    def cancel(self,run_id,owner):
        with self.lock:
            value=self.workers.get(run_id)
            if value and value['owner']==owner:
                value['cancel'].set();return True
            return False

    def shutdown(self):
        with self.lock:
            workers=list(self.workers.values())
            for value in workers:value['cancel'].set()
        for value in workers:value['thread'].join(timeout=10)


REPLAYS=ReplayRegistry()

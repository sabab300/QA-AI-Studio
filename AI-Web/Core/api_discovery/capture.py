"""One worker owns one nonpersistent browser and its bounded capture lifecycle.

No raw HAR, browser profile, cookies, screenshot or full DOM is written to disk.
Chromium DNS is pinned to the publicly validated address. Cross-origin traffic,
redirects and websockets are blocked; service workers cannot bypass routing.
"""
import ipaddress
import socket
import threading
import time
import os
import uuid
import queue
from urllib.parse import urlsplit

from .evidence import DiscoveryError, normalize_entry

_DNS_SLOTS=threading.BoundedSemaphore(2)


def resolve_addresses(host,port,timeout=5):
    """Bound resolver waits and retained workers, even if OS DNS never returns."""
    if not _DNS_SLOTS.acquire(blocking=False):raise DiscoveryError('DNS resolver capacity is busy; retry after the pending lookup completes.')
    result=queue.Queue(maxsize=1)
    def lookup():
        try:result.put((True,list({i[4][0] for i in socket.getaddrinfo(host,port,proto=socket.IPPROTO_TCP)})))
        except Exception:result.put((False,None))
        finally:_DNS_SLOTS.release()
    threading.Thread(target=lookup,name='api-discovery-dns',daemon=True).start()
    try:ok,addresses=result.get(timeout=timeout)
    except queue.Empty:raise DiscoveryError('DNS lookup exceeded the bounded timeout; no request was sent.') from None
    if not ok or not addresses:raise DiscoveryError('DNS lookup failed; no request was sent.')
    return addresses


def resolve_target(url, allow_local=False):
    from Core.web_fetcher import check_public_url, UnsafeURL
    p = urlsplit(url)
    try:
        if p.scheme not in {'http','https'} or not p.hostname or p.username or p.password:raise ValueError()
        addresses = resolve_addresses(p.hostname,p.port or (443 if p.scheme=='https' else 80))
        loopback = allow_local and os.environ.get("QA_API_DISCOVERY_ALLOW_LOCAL_TARGETS") == "1" and all(ipaddress.ip_address(a).is_loopback for a in addresses)
        if not loopback:
            check_public_url(url, resolver=lambda host: addresses)
        public_v4 = [a for a in addresses if ipaddress.ip_address(a).version == 4]
        if not public_v4:
            raise DiscoveryError("This capture engine currently requires a public IPv4 target.")
        if p.port and p.port not in {80, 443} and not loopback:
            raise DiscoveryError("Browser discovery currently permits standard HTTP/HTTPS ports only.")
        return p.hostname.encode("idna").decode("ascii").lower(), sorted(public_v4)[0]
    except (ValueError, OSError, UnicodeError, UnsafeURL):
        raise DiscoveryError("Target must resolve exclusively to public addresses; local/private/metadata targets are not permitted.") from None


class BrowserCapture:
    def __init__(self, repository, identity, owner, url, host, address, duration=300,
                 planner=None, headless=False,capture_samples=False,resource_origins=None,target_check=None):
        self.repository, self.identity, self.owner = repository, identity, owner
        self.url, self.host, self.address = url, host, address
        self.duration = duration
        self.stop_event = threading.Event()
        self.cancelled = False
        self.paused = threading.Event()
        self.thread = threading.Thread(target=self.run, name="api-discovery-" + identity[:8], daemon=True)
        self._error = None
        self.planner, self.headless = planner, headless
        self.capture_samples=capture_samples
        self.resource_origins=resource_origins or []
        self.blocked_resources=set()
        self.target_check=target_check
        self.current_action = None
        self.pending_action = None
        self.decisions = {}
        self._decision_lock = threading.Lock()
        self._agent_click = False
        self.runtime_stage = "created"

    def start(self):
        self.thread.start()

    def run(self):
        try:
            if self.stop_event.is_set():
                self.repository.update(self.identity,self.owner,'cancelled' if self.cancelled else 'completed');return
            self.runtime_stage = "starting_playwright"
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                self.runtime_stage = "launching_chromium"
                pins={self.host:self.address};pins.update({item['host']:item['address'] for item in self.resource_origins})
                browser = playwright.chromium.launch(headless=self.headless, timeout=15000, args=[
                    "--host-resolver-rules="+', '.join('MAP '+host+' '+address for host,address in pins.items())+", MAP * ~NOTFOUND",
                    "--disable-quic", "--disable-background-networking", "--no-proxy-server",
                ])
                try:
                    self.runtime_stage = "creating_context"
                    context = browser.new_context(service_workers="block", accept_downloads=False)
                    try:
                        # Fail closed if the installed Playwright cannot block WebSockets.
                        if not hasattr(context, "route_web_socket"):
                            raise DiscoveryError("Upgrade the installed Playwright to a version supporting WebSocket routing before browser discovery.")
                        context.route_web_socket("**/*", lambda ws: ws.close())
                        context.route("**/*", self.route)
                        samples=None
                        if self.capture_samples:
                            from .browser_samples import BrowserSamples
                            samples=BrowserSamples(self);context.on('page',samples.attach)
                        else:
                            context.on("response", self.response)
                            context.on("requestfailed", self.request_failed)
                        context.expose_binding("qaApiDiscoveryAction", self.manual_action)
                        context.add_init_script('''document.addEventListener('click',e=>{
                          if(e.isTrusted) window.qaApiDiscoveryAction({kind:'click',tag:e.target.tagName}).catch(()=>{});
                        },true);''')
                        self.repository.update(self.identity, self.owner, "recording")
                        self.runtime_stage = "navigating"
                        page = self.navigate_initial(context)
                        self.runtime_stage = "collecting"
                        deadline = time.monotonic() + self.duration
                        previous = []
                        while not self.stop_event.is_set() and time.monotonic() < deadline and context.pages:
                            try:
                                if self.planner and not self.paused.is_set():
                                    if len(previous) >= 20:
                                        raise DiscoveryError("Agent reached its 20-action limit. Captured evidence was preserved.")
                                    if not self.agent_step(context.pages[0], previous, deadline):
                                        break
                                context.pages[0].wait_for_timeout(100)
                            except Exception as error:
                                if not context.pages:
                                    break
                                raise error
                        if self._error:
                            raise self._error
                    finally:
                        self.runtime_stage = "closing_context"
                        if samples:samples.close()
                        context.close()
                finally:
                    browser.close()
            self.repository.update(self.identity, self.owner, "cancelled" if self.cancelled else "completed")
            self.runtime_stage = "closed"
        except Exception as error:
            message = 'Recording memory allocation failed. Safely captured evidence remains available; close unused applications before retrying.' if isinstance(error,MemoryError) else str(error) if isinstance(error, DiscoveryError) else "Browser capture ended unexpectedly (" + type(error).__name__ + "). Safely captured evidence remains available."
            self.repository.update(self.identity, self.owner, "failed", error=message)
            self.runtime_stage = "failed"

    def navigate_initial(self, context):
        page = context.new_page()
        page.goto(self.url, wait_until="domcontentloaded", timeout=10000)
        return page

    def route(self, route):
        p = urlsplit(route.request.url)
        try:
            origin = urlsplit(self.url)
            valid = (p.scheme == origin.scheme and p.hostname == self.host and
                     (p.port or (443 if p.scheme == "https" else 80)) == (origin.port or (443 if origin.scheme == "https" else 80)) and
                     not p.username and not p.password)
        except ValueError:
            valid = False
        if valid:
            route.continue_()
        elif route.request.resource_type in {'image','stylesheet','font','script','media'} and any(
            p.scheme==item['scheme'] and p.hostname==item['host'] and (p.port or (443 if p.scheme=='https' else 80))==item['port'] and not p.username and not p.password for item in self.resource_origins):
            route.continue_()
        else:
            if len(self.blocked_resources)<20 and p.hostname:self.blocked_resources.add(p.scheme+'://'+p.hostname)
            route.abort("blockedbyclient")

    def response(self, response):
        if self.paused.is_set() or self.stop_event.is_set() or response.request.resource_type not in {"xhr", "fetch"}:
            return
        try:
            request = response.request
            # Do not call response.body(): Content-Length is untrusted and an
            # unbounded read can allocate a huge body. Browser mode is metadata-only.
            entry = {"request": {"method": request.method, "url": request.url,
                      "headers": [{"name": k, "value": v} for k, v in request.headers.items()]},
                     "response": {"status": response.status,
                                  "content": {"mimeType": response.headers.get("content-type", "")}}}
            evidence=normalize_entry(entry, "Browser", self.current_action)
            evidence['traffic_classification']='First-party'
            timing=request.timing
            evidence['duration_ms']=max(0,min(3600000,timing.get('responseEnd',0)))
            if self.capture_samples and request.method in {'POST','PUT','PATCH'}:
                from .samples import parse_sample
                try:
                    body,mode=parse_sample(request.post_data or '',request.headers.get('content-type',''))
                    evidence.update(request_sample=body,request_body_mode=mode,sample_classification='Sanitized request sample; credentials replaced with variable references.')
                except DiscoveryError:evidence['limitations'].append('Request sample omitted: unsupported, sensitive or exceeds safe capture limits.')
            if self.capture_samples and urlsplit(request.url).query:
                from urllib.parse import parse_qsl
                from .samples import sanitize_sample
                try:
                    pairs=parse_qsl(urlsplit(request.url).query,keep_blank_values=True,max_num_fields=60)
                    evidence['query_sample']=[{'name':name,'value':sanitize_sample({name:value})[name]} for name,value in pairs]
                except (DiscoveryError,ValueError):evidence['limitations'].append('Query sample omitted: unsafe fields or capture limit.')
            self.repository.append(self.identity, self.owner,evidence)
        except DiscoveryError as error:
            self._error = error
            self.stop_event.set()
        except Exception:
            self._error = DiscoveryError("A request could not be safely captured or persisted. Collection stopped; committed evidence is retained.")
            self.stop_event.set()

    def request_failed(self, request):
        if self.paused.is_set() or self.stop_event.is_set() or request.resource_type not in {'xhr','fetch'}:
            return
        try:
            evidence=normalize_entry({'request':{'method':request.method,'url':request.url},
                'response':{'status':0}},'Browser',self.current_action)
            evidence.update(network_error=True,error_category='NETWORK_FAILURE',
                traffic_classification='First-party' if urlsplit(request.url).hostname==self.host else 'Third-party (blocked)')
            # Browser failure strings can contain target data; retain only a category.
            self.repository.append(self.identity,self.owner,evidence)
        except Exception:
            self._error=DiscoveryError('A failed request could not be safely persisted. Collection stopped; committed evidence remains available.')
            self.stop_event.set()

    def manual_action(self, source, payload):
        if self.paused.is_set() or self.stop_event.is_set() or self._agent_click:
            return
        if not isinstance(payload, dict) or payload.get("kind") != "click":
            return
        from .evidence import safe_url
        page = source.get("page")
        if page and urlsplit(page.url).hostname == self.host:
            # No text, field values, credentials or full DOM enters the action log.
            self.current_action = self.repository.action(self.identity, self.owner,
                {"kind": "click", "source": "Manual", "page": safe_url(page.url)}, "captured")

    def decide(self, action_id, approved,control_id=None):
        with self._decision_lock:
            if action_id != self.pending_action:
                raise DiscoveryError("That action is stale or is not awaiting confirmation.")
            self.decisions[action_id] = {'approved':approved,'control_id':control_id}

    def agent_step(self, page, previous, deadline):
        from .agent import bounded_plan
        from .evidence import safe_url
        from Core.ai_platform.sanitize import scrub
        locator=page.locator('a[href],button,input[type="submit"],[role="button"]')
        handles = [locator.nth(index).element_handle(timeout=1000) for index in range(min(locator.count(),40))]
        handles = [handle for handle in handles if handle is not None]
        controls, by_id = [], {}
        page_url = page.url
        try:
            for handle in handles:
                if not handle.is_visible() or not handle.is_enabled():
                    continue
                descriptor = handle.evaluate("e=>({tag:e.tagName,name:(e.getAttribute('aria-label')||e.getAttribute('title')||e.innerText||'').slice(0,160)})")
                control_id = uuid.uuid4().hex[:12]
                controls.append({"id": control_id, "tag": descriptor["tag"], "name": scrub(descriptor["name"], limit=160)})
                by_id[control_id] = (handle, descriptor)
            plan = bounded_plan(self.planner, controls, previous, page, self.stop_event,
                                timeout=max(1, min(60, deadline-time.monotonic())))
            if plan["action"] == "finish":
                return False
            handle, descriptor = by_id[plan["control_id"]]
            action_id = self.repository.action(self.identity, self.owner,
                {"kind": "click", "source": "AI Agent", "page": safe_url(page_url),
                 "control": next(c for c in controls if c["id"] == plan["control_id"]),'alternatives':controls,
                 "reason": scrub(plan["reason"], limit=300),
                 "notice": "This action may modify data. Explicit approval is required."}, "awaiting_confirmation")
            self.pending_action = action_id
            approved = None
            while not self.stop_event.is_set() and time.monotonic() < deadline:
                with self._decision_lock:
                    if action_id in self.decisions:
                        approved = self.decisions.pop(action_id)
                        break
                page.wait_for_timeout(100)
            self.pending_action = None
            if isinstance(approved,dict):
                chosen=approved.get('control_id') or plan['control_id'];approved=approved['approved']
                if chosen not in by_id:raise DiscoveryError('Edited action must identify a currently observed control.')
                if approved and chosen!=plan['control_id']:
                    handle,descriptor=by_id[chosen]
                    self.repository.review_action(self.identity,self.owner,action_id,next(c for c in controls if c['id']==chosen))
            while self.paused.is_set() and not self.stop_event.is_set() and time.monotonic()<deadline:
                page.wait_for_timeout(100)
            if self.stop_event.is_set() or time.monotonic()>=deadline:
                approved = None
            if approved is not True:
                self.repository.action_status(self.identity, self.owner, action_id, "rejected" if approved is False else "cancelled")
                if approved is False:
                    previous.append({'kind':'rejected','name':descriptor['name']});return True
                return False
            if page.url != page_url or not handle.evaluate("e=>e.isConnected") or not handle.is_visible() or not handle.is_enabled():
                self.repository.action_status(self.identity, self.owner, action_id, "stale")
                raise DiscoveryError("Page/control changed while awaiting confirmation. Nothing was clicked; start a new plan.")
            if self.target_check:self.target_check(page.url)
            live = handle.evaluate("e=>({tag:e.tagName,name:(e.getAttribute('aria-label')||e.getAttribute('title')||e.innerText||'').slice(0,160)})")
            if live != descriptor:
                self.repository.action_status(self.identity, self.owner, action_id, "stale")
                raise DiscoveryError("Control identity changed; the proposed click was rejected.")
            self.current_action = action_id
            self._agent_click = True
            try:
                handle.click(timeout=5000)
                page.wait_for_timeout(300)
            finally:
                self._agent_click = False
            self.repository.action_status(self.identity, self.owner, action_id, "executed")
            previous.append({"kind": "click", "name": scrub(descriptor["name"], limit=160), "page": safe_url(page_url)})
            return True
        finally:
            for handle in handles:
                handle.dispose()


class CaptureRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._items = {}

    def start(self, repository, identity, owner, url, host, address, duration, planner=None,capture_samples=False,resource_origins=None,target_check=None):
        with self._lock:
            self._items = {k: v for k, v in self._items.items() if v.thread.is_alive()}
            if len(self._items) >= 2 or any(v.owner == owner for v in self._items.values()):
                raise DiscoveryError("A capture is already active, or the two-browser resource limit was reached.")
            capture = BrowserCapture(repository, identity, owner, url, host, address, duration, planner=planner,capture_samples=capture_samples,resource_origins=resource_origins,target_check=target_check)
            self._items[identity] = capture
            capture.start()

    def control(self, identity, owner, action):
        with self._lock:
            capture = self._items.get(identity)
            if not capture or capture.owner != owner or not capture.thread.is_alive():
                raise DiscoveryError("This browser is no longer running. Open persisted results or start a new discovery.")
            with capture.repository.connection() as connection:
                row=connection.execute('SELECT status FROM api_discovery_sessions WHERE id=? AND owner_id=?',(identity,owner)).fetchone()
            state=row[0] if row else ''
            if action in {"stop","cancel"}:
                capture.cancelled = action == 'cancel'
                capture.repository.update(identity,owner,'stopping')
                capture.stop_event.set()
            elif action == "pause":
                if state!='recording' or capture.paused.is_set() or capture.stop_event.is_set():raise DiscoveryError('Pause is not valid in the current recording state.')
                capture.paused.set()
                capture.repository.update(identity, owner, "paused")
            elif action == "resume":
                if state!='paused' or not capture.paused.is_set() or capture.stop_event.is_set():raise DiscoveryError('Resume is not valid in the current recording state.')
                capture.repository.update(identity, owner, "recording")
                capture.paused.clear()

    def decide(self, identity, owner, action_id, approved,control_id=None):
        with self._lock:
            capture = self._items.get(identity)
            if not capture or capture.owner != owner or not capture.thread.is_alive():
                raise DiscoveryError("The agent browser is no longer running.")
            capture.decide(action_id, approved,control_id)

    def shutdown(self):
        with self._lock:
            captures = list(self._items.values())
            for capture in captures:
                capture.stop_event.set()
        for capture in captures:
            capture.thread.join(timeout=30)


CAPTURES = CaptureRegistry()

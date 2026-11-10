"""Safe report exports from persisted assertion evidence, never AI verdicts."""
import html
import io
import json


def render(result,format):
    if format=='json':return json.dumps(result,ensure_ascii=False,indent=2).encode(),'application/json'
    rows=[]
    for item in result.get('results',[]):
        for assertion in item.get('assertions',[]) or [{}]:
            rows.append([item.get('name',item.get('id','')),item.get('method',''),item.get('status',''),
                         assertion.get('type',''),json.dumps(assertion.get('expected')),json.dumps(assertion.get('actual')),assertion.get('status',''),assertion.get('reason',item.get('reason',''))])
    headings=['API','Method','Result','Assertion','Expected','Actual','Assertion result','Reason']
    hierarchy=result.get('hierarchy',{})
    summary=' → '.join(str(hierarchy.get(key,'')) for key in ('domain','module','knowledge_name','document_type','version','source_type','source_document'))
    summary+=' · Total APIs: '+str(len(result.get('results',[])))+' · '+json.dumps(result.get('counts',{}))
    summary+=' · '+str(result.get('environment',''))+' · '+str(result.get('executed_by_name',''))+' · '+str(result.get('executed_at',''))
    evidence=[(item.get('name',item.get('id','')),json.dumps({'request':item.get('request',{}),'response':item.get('response',{}),'notice':item.get('evidence_notice','')},ensure_ascii=False)) for item in result.get('results',[])]
    if format=='html':
        text='<!doctype html><html><head><meta charset="utf-8"><title>API Discovery execution</title></head><body><h1>API Discovery execution</h1><p>'+html.escape(str(result.get('status','')))+' · '+html.escape(str(result.get('run_id','')))+' · Version '+html.escape(str(result.get('version','')))+' </p><table border="1"><thead><tr>'
        text=text.replace('<table border="1">','<p>'+html.escape(summary)+'</p><p>'+html.escape(str(result.get('environment','')))+' · '+html.escape(str(result.get('executed_by_name','')))+' · '+html.escape(str(result.get('executed_at','')))+'</p><table border="1">')
        text+=''.join('<th>'+html.escape(value)+'</th>' for value in headings)+'</tr></thead><tbody>'
        text+=''.join('<tr>'+''.join('<td>'+html.escape(str(value))+'</td>' for value in row)+'</tr>' for row in rows)
        text+='</tbody></table><h2>Sanitized request/response evidence</h2>'
        for name,value in evidence:
            text+='<details><summary>'+html.escape(str(name))+'</summary><pre>'+html.escape(value[:6000])+('</pre><p>Display limited to 6000 characters; see JSON report.</p>' if len(value)>6000 else '</pre>')+'</details>'
        return (text+'</body></html>').encode(),'text/html; charset=utf-8'
    if format=='xlsx':
        from openpyxl import Workbook
        book=Workbook();sheet=book.active;sheet.title='API execution';sheet.append(headings)
        metadata=book.create_sheet('Summary')
        for key,value in {**hierarchy,'collection_name':result.get('collection_name',''),'collection_version':result.get('version',''),'environment':result.get('environment',''),'executed_by':result.get('executed_by_name',''),'executed_at':result.get('executed_at',''),'counts':json.dumps(result.get('counts',{}))}.items():
            text=str(value);metadata.append([str(key),"'"+text if text.startswith(('=','+','-','@')) else text])
        for row in rows:sheet.append(["'"+str(value) if str(value).startswith(('=','+','-','@')) else str(value) for value in row])
        details=book.create_sheet('Sanitized Evidence');details.append(['API','Request / Response','Notice'])
        for name,value in evidence:details.append(["'"+str(name),value[:6000],'Display limited; full retained evidence is in JSON report.' if len(value)>6000 else 'Sanitized evidence'])
        sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
        for column in sheet.columns:sheet.column_dimensions[column[0].column_letter].width=30
        output=io.BytesIO();book.save(output);book.close()
        return output.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    if format=='pdf':
        from reportlab.lib.pagesizes import A4,landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate,Paragraph,Table,TableStyle
        from reportlab.lib import colors
        output=io.BytesIO();styles=getSampleStyleSheet()
        content=[Paragraph('API Discovery execution — '+html.escape(str(result.get('status',''))),styles['Heading1']),Paragraph(html.escape(summary),styles['BodyText'])]
        values=[[Paragraph(html.escape(str(value)[:360]),styles['BodyText']) for value in row] for row in [headings]+rows]
        table=Table(values,repeatRows=1,colWidths=[90,40,50,75,110,110,55,190]);table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),colors.lightgrey),('GRID',(0,0),(-1,-1),.3,colors.grey)]))
        content.append(table)
        content.append(Paragraph('Sanitized request/response evidence',styles['Heading2']))
        for name,value in evidence:
            content.append(Paragraph(html.escape(str(name)),styles['Heading3']))
            content.append(Paragraph(html.escape(value[:2000]),styles['BodyText']))
            if len(value)>2000:content.append(Paragraph('Display limited to 2000 characters; see JSON report.',styles['BodyText']))
        SimpleDocTemplate(output,pagesize=landscape(A4),leftMargin=10,rightMargin=10).build(content)
        return output.getvalue(),'application/pdf'
    raise ValueError('Unsupported report format.')


def compare(baseline,current):
    from .evidence import shape
    old={item['id']:item for item in baseline.get('results',[])};new={item['id']:item for item in current.get('results',[])}
    groups={'new_failures':[],'resolved_failures':[],'unchanged_results':[],'needs_review':[]}
    for identity in sorted(set(old)|set(new)):
        before=old.get(identity);after=new.get(identity)
        if before is None or after is None:key='needs_review'
        elif before['status']!='FAIL' and after['status']=='FAIL':key='new_failures'
        elif before['status']=='FAIL' and after['status']=='PASS':key='resolved_failures'
        elif before['status']==after['status']:
            previous_shape=shape(before.get('response',{}).get('json'))
            current_shape=shape(after.get('response',{}).get('json'))
            previous_checks=[(item.get('id'),item.get('status'),item.get('expected')) for item in before.get('assertions',[])]
            current_checks=[(item.get('id'),item.get('status'),item.get('expected')) for item in after.get('assertions',[])]
            key='unchanged_results' if previous_shape==current_shape and previous_checks==current_checks else 'needs_review'
        else:key='needs_review'
        groups[key].append({'id':identity,'previous':before.get('status') if before else None,'current':after.get('status') if after else None})
    return groups

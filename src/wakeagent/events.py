import pathlib, json
def mailbox_events(base_dir):
    base_dir=pathlib.Path(base_dir);events=[]
    for mb in base_dir.iterdir():
        if not mb.is_dir(): continue
        pending=mb/'pending'
        if not pending.exists(): continue
        for f in pending.glob('*.json'):
            try:
                data=json.loads(f.read_text())
                events.append({'source':'mailbox','agent':mb.name,'msg_id':data.get('id'),'from':data.get('from'),'body':data.get('body'),'attachment_folder':data.get('attachment_folder_container'),'file':str(f)})
            except: pass
    return events

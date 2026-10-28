import json
from pathlib import Path
import sqlite3
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
db=sqlite3.connect(ROOT/'data/local-finance.sqlite3');db.row_factory=sqlite3.Row
groups={};images=[]
for row in db.execute('SELECT * FROM local_telegram_messages ORDER BY id'):
    message=json.loads(row['message_json'])
    if not message.get('photo'):continue
    group=message.get('media_group_id') or 'ungrouped'
    groups.setdefault(group,[]).append(row['id'])
    path=ROOT/'data/documents/inbox'/f"{row['id']}.bin"
    if path.exists():
        review=ROOT/'data/documents/review';review.mkdir(parents=True,exist_ok=True)
        with Image.open(path) as img:img.save(review/f'photo-{len(images)+1}.png')
    images.append({'original_saved':path.exists(),'has_caption':bool(message.get('caption')),'resolutions':len(message['photo'])})
print(json.dumps({'photo_count':len(images),'album_sizes':[len(v) for v in groups.values()],'images':images}))
db.close()

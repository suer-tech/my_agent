import argparse
import json
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from finance.core import Ledger
from finance.delivery import deliver,telegram


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db',required=True)
    parser.add_argument('--today',help='YYYY-MM-DD; по умолчанию Asia/Yekaterinburg')
    parser.add_argument('--send',action='store_true',help='Без флага только dry-run')
    args = parser.parse_args()
    today = datetime.fromisoformat(args.today).date() if args.today else datetime.now(ZoneInfo('Asia/Yekaterinburg')).date()
    result = deliver(Ledger(args.db),today.isoformat(),(today+timedelta(days=366)).isoformat(),
                     (lambda text:telegram('sendMessage',{'text':text})) if args.send else None)
    print(json.dumps(result,ensure_ascii=False))
    if any(r.get('state')=='uncertain' for r in result): raise SystemExit(2)

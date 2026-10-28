"""Daily consistent backup/14-day source retention for the isolated HR ledger."""
import json
from maintenance import maintain

if __name__=='__main__':
    print(json.dumps(maintain('/opt/iva/data/lead-flow/hr-research.sqlite3', [],
                              '/opt/iva/data/lead-flow/hr-backups')))

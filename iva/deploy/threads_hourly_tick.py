"""Demand signal for an allowlisted Threads campaign; never launches Chromium."""
import argparse
import sys,time
sys.path[:0]=['/usr/local/lib/iva-browser','/opt/iva']
from iva_integration import threads_hourly as worker


def queue_campaign(campaign,request=None):
    if campaign not in ('extension','development'):raise ValueError('Unknown Threads campaign')
    if request is None:
        import browser_turn
        request=browser_turn.request
    root=worker.ROOT if campaign=='extension' else worker.ROOT/'development'
    launch=worker.read('hourly-launch.json',{},root=root)
    if launch.get('enabled'):
        launch['pending']=True;launch['last_requested_at']=time.time();worker.write('hourly-launch.json',launch,root=root)
        # Scheduling cannot override an interactive browser or a research lease.
        # The coordinator resumes queued background work at a safe boundary.
        request({'action':'enqueue','task':'threads'})
        print('Threads work queued: '+campaign)
    else:print('Threads campaign is paused: '+campaign)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--campaign',choices=['extension','development'],default='extension')
    queue_campaign(parser.parse_args().campaign)


if __name__=='__main__':main()

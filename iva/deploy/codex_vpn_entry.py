#!/usr/bin/python3
"""Unprivileged Codex executable shim; arguments/stdin pass through unchanged."""
import os
import sys
home=os.environ.get('CODEX_HOME','/home/iva/.codex')
os.execv('/usr/bin/sudo',['sudo','-n','/usr/local/sbin/iva-codex-netns',home,str(os.getpid()),*sys.argv[1:]])

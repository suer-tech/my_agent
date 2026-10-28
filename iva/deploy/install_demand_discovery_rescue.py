"""Narrow follow-up release using the verified scheduler-only installer.

The full supervised third attempt must finish before installation.  Existing
code, evidence, schedules, root helpers and notification policy are preserved.
"""
from pathlib import Path

from deploy import install_demand_content_quality as installer


RELEASE = Path('/opt/iva/data/demand-discovery-rescue-release-20261026')
FILES = [
    'iva_integration/demand_research.py',
    'iva_integration/demand_discovery_rescue.py',
    'tests/test_demand_discovery_rescue.py',
    'tests/test_demand_research.py',
    'tests/test_demand_hypothesis_context.py',
    'deploy/verify_demand_discovery_rescue.py',
    'tests/test_verify_demand_discovery_rescue.py',
    'deploy/install_demand_discovery_rescue.py',
]
PATTERNS = (
    'test_demand_discovery_rescue.py', 'test_verify_demand_discovery_rescue.py',
    'test_demand_hypothesis_context.py',
    'test_demand_research.py', 'test_demand_agents.py', 'test_demand_scope.py',
    'test_demand_request_keys.py', 'test_demand_quote_repair.py',
    'test_demand_sources.py', 'test_demand_content_quality.py',
    'test_run_supervised_demand.py', 'test_install_demand_content_quality.py',
)


def main():
    installer.RELEASE = RELEASE
    installer.FILES = FILES
    installer.PATTERNS = PATTERNS
    installer.REQUIRED_ATTEMPT = 3
    installer.main()


if __name__ == '__main__':
    main()

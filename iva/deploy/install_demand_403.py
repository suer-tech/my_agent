"""Install the owner-requested HTTP 403 VPN retry at an idle scheduler boundary."""
from pathlib import Path

from deploy import install_demand_content_quality as installer

RELEASE = Path('/opt/iva/data/demand-http403-release-20261026')
FILES = [
    'iva_integration/demand_network.py',
    'iva_integration/demand_research.py',
    'tests/test_demand_network.py',
    'deploy/verify_demand_403.py',
    'tests/test_verify_demand_403.py',
    'deploy/install_demand_403.py',
]
PATTERNS = (
    'test_demand_network.py', 'test_verify_demand_403.py',
    'test_demand_sources.py', 'test_demand_network_install.py',
    'test_demand_research.py', 'test_demand_coverage_integration.py',
    'test_demand_coverage_runtime.py', 'test_demand_evidence_coverage.py',
    'test_demand_scope.py', 'test_demand_discovery_rescue.py',
    'test_install_demand_content_quality.py', 'test_run_supervised_demand.py',
)


def main():
    installer.RELEASE = RELEASE
    installer.FILES = FILES
    installer.PATTERNS = PATTERNS
    installer.REQUIRED_ATTEMPT = 3
    installer.main()


if __name__ == '__main__':
    main()

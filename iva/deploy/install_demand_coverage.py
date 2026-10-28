"""Install evidence-coverage checkpoints with the existing idle scheduler barrier.

Only reviewed application sources are replaced. The periodic research schedule
stays paused; this installer neither resumes historical runs nor changes helpers.
"""
from pathlib import Path

from deploy import install_demand_content_quality as installer


RELEASE = Path('/opt/iva/data/demand-coverage-release-20261026')
FILES = [
    'iva_integration/demand_research.py',
    'iva_integration/demand_coverage_runtime.py',
    'iva_integration/demand_evidence_coverage.py',
    'iva_integration/scheduled_tasks.py',
    'tests/test_demand_coverage_runtime.py',
    'tests/test_demand_coverage_integration.py',
    'tests/test_demand_evidence_coverage.py',
    'tests/test_demand_research.py',
    'tests/test_demand_discovery_rescue.py',
    'deploy/verify_demand_coverage.py',
    'tests/test_verify_demand_coverage.py',
    'deploy/install_demand_coverage.py',
]
PATTERNS = (
    'test_demand_evidence_coverage.py', 'test_demand_coverage_runtime.py',
    'test_demand_coverage_integration.py',
    'test_verify_demand_coverage.py', 'test_demand_discovery_rescue.py',
    'test_verify_demand_discovery_rescue.py', 'test_demand_hypothesis_context.py',
    'test_demand_research.py', 'test_demand_agents.py', 'test_demand_scope.py',
    'test_demand_request_keys.py', 'test_demand_quote_repair.py',
    'test_demand_sources.py', 'test_demand_content_quality.py',
    'test_run_supervised_demand.py', 'test_install_demand_content_quality.py',
    'test_demand_scheduler.py', 'test_scheduled_tasks.py',
    'test_demand_retention.py',
)


def main():
    installer.RELEASE = RELEASE
    installer.FILES = FILES
    installer.PATTERNS = PATTERNS
    installer.REQUIRED_ATTEMPT = 3
    installer.main()


if __name__ == '__main__':
    main()

# Code generated from extension_registry.json by tools/generate_extensions.py. DO NOT EDIT.

EXTENSION_RULES = {
    'jobseek.runtime.v1/representative-json/monitor-config': {
        'version': 1,
        'encoding': 2,
        'contexts': frozenset(('manifest',)),
        'validator': 'monitor_config',
    },
    'jobseek.runtime.v1/representative-json/scraper-config': {
        'version': 1,
        'encoding': 2,
        'contexts': frozenset(('manifest',)),
        'validator': 'scraper_config',
    },
    'jobseek.runtime.v1/representative-json/runtime-metadata': {
        'version': 1,
        'encoding': 2,
        'contexts': frozenset(('job_content', 'monitor_metadata',)),
        'validator': 'runtime_metadata',
    },
    'jobseek.runtime.v1/browser/evaluation-json': {
        'version': 1,
        'encoding': 2,
        'contexts': frozenset(('browser_evaluation',)),
        'validator': 'evaluation_json',
    },
}

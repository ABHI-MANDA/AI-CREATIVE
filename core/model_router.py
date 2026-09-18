from . import config

REGISTRY = {
    'image': [{'id': 'openrouter-image', 'label': f'OpenRouter {config.OPENROUTER_IMAGE_MODEL}', 'kind': 'hosted'}, {'id': 'local-pillow', 'label': 'Local fallback', 'kind': 'local'}],
    'video': [{'id': 'openrouter-video', 'label': f'OpenRouter {config.OPENROUTER_VIDEO_MODEL}', 'kind': 'hosted'}, {'id': 'local-opencv', 'label': 'Local fallback video', 'kind': 'local'}],
    'copy': [{'id': 'openrouter-text', 'label': f'OpenRouter {config.OPENROUTER_TEXT_MODEL}', 'kind': 'hosted'}, {'id': 'local-copy', 'label': 'Local fallback', 'kind': 'local'}],
}

def available_models(creative_type):
    return [{**model, 'usable': model['kind'] == 'local' or bool(config.OPENROUTER_API_KEY)} for model in REGISTRY.get(creative_type, [])]

def select_model(creative_type, prompt=''):
    options = available_models(creative_type)
    return next((model for model in options if model['usable'] and model['kind'] == 'hosted'), next(model for model in options if model['usable']))

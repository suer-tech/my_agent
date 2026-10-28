"""Validate an entire routing step before returning any executable tool calls."""
import json
import jsonschema
from local_finance.codex_cli import StructuredOutputError


def strict_object(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('Non-finite JSON number')
    value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(value, dict):
        raise ValueError('JSON object required')
    return value


def validate_step(value, schema, tools):
    jsonschema.validate(value, schema)
    for call in value['calls']:
        if call['name'] not in tools:
            raise ValueError('Unlisted tool')
        args = strict_object(call['arguments_json'])
        jsonschema.validate(args, tools[call['name']]['parameters'])
        # Finance uses another serialized object inside the tool arguments.
        # Validate it now, before Eve can execute even the first call in a batch.
        if 'finance' in call['name'] and 'payload_json' in args:
            strict_object(args['payload_json'])
    return value


def validated_routing(cli, prompt, schema, tools, images=(), transform=lambda value: value):
    total = {'input_tokens': 0, 'output_tokens': 0, 'cached_input_tokens': 0}
    for attempt in range(2):
        try:
            value = cli.structured(prompt, schema, images=images)
            validate_step(value, schema, tools)
            value = transform(value)
            validate_step(value, schema, tools)
        except (StructuredOutputError, ValueError, TypeError, KeyError, jsonschema.ValidationError):
            if attempt:
                raise StructuredOutputError('Ива не получила корректный JSON после двух попыток; этот шаг инструментов не выполнялся') from None
            # The CLI only generates routing data. No tool has run, so retrying
            # this one model step cannot repeat an already completed tool step.
            prompt += '\nVALIDATION FAILED: return strict JSON matching the schema. All arguments_json and finance payload_json strings must encode JSON OBJECTS. Use only listed tools. No tool from the rejected response has executed. Do not repeat earlier successful tool calls from the conversation.'
        else:
            return value, total, attempt
        finally:
            usage = getattr(cli, 'last_usage', None) or {}
            for key in total:
                total[key] += usage.get(key, 0) if type(usage.get(key, 0)) is int else 0

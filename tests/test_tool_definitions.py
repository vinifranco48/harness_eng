import json
import unittest

from apps.web.api.tools.definitions import (
    ANALYSIS_CREATE_TOOL_NAME,
    analysis_create_definition,
)


class ToolDefinitionsTests(unittest.TestCase):

    def test_analysis_create_public_contract(self):
        definition = analysis_create_definition()

        self.assertEqual(ANALYSIS_CREATE_TOOL_NAME, "analysis_create")
        self.assertEqual(definition["name"], ANALYSIS_CREATE_TOOL_NAME)
        self.assertIsInstance(definition["description"], str)
        self.assertTrue(definition["description"].strip())

        schema = definition["inputSchema"]
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["required"], ["title"])
        self.assertIs(schema["additionalProperties"], False)
        title = schema["properties"]["title"]
        self.assertEqual(title["type"], "string")
        self.assertEqual(title["minLength"], 1)
        self.assertEqual(title["maxLength"], 120)

    def test_definition_is_json_serializable(self):
        definition = analysis_create_definition()

        encoded = json.dumps(definition, ensure_ascii=False, allow_nan=False)

        self.assertEqual(json.loads(encoded), definition)

    def test_calls_do_not_share_mutable_definitions(self):
        first = analysis_create_definition()
        second = analysis_create_definition()

        first["name"] = "changed"
        first["inputSchema"]["required"].clear()
        first["inputSchema"]["properties"]["title"]["maxLength"] = 1

        for definition in (second, analysis_create_definition()):
            self.assertEqual(definition["name"], ANALYSIS_CREATE_TOOL_NAME)
            self.assertEqual(definition["inputSchema"]["required"], ["title"])
            self.assertEqual(
                definition["inputSchema"]["properties"]["title"]["maxLength"],
                120,
            )

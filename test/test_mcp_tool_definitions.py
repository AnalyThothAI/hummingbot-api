import unittest

from mcp.tools import tool_definitions


class McpToolDefinitionsTests(unittest.TestCase):
    def test_deploy_v2_controllers_schema_includes_unique_and_defaults(self):
        definitions = {tool["name"]: tool for tool in tool_definitions()}
        schema = definitions["bot_deploy_v2_controllers"]["inputSchema"]["properties"]

        self.assertIn("unique_instance_name", schema)
        self.assertIn("apply_gateway_defaults", schema)

    def test_deploy_v2_script_schema_includes_apply_gateway_defaults(self):
        definitions = {tool["name"]: tool for tool in tool_definitions()}
        schema = definitions["bot_deploy_v2_script"]["inputSchema"]["properties"]

        self.assertIn("apply_gateway_defaults", schema)

    def test_workflow_plan_schema_includes_unique_and_defaults(self):
        definitions = {tool["name"]: tool for tool in tool_definitions()}
        schema = definitions["deploy_v2_workflow_plan"]["inputSchema"]["properties"]

        self.assertIn("unique_instance_name", schema)
        self.assertIn("apply_gateway_defaults", schema)

    def test_gateway_tools_include_networks_and_swaps(self):
        definitions = {tool["name"]: tool for tool in tool_definitions()}

        for name in (
            "gateway_chains",
            "gateway_networks",
            "gateway_network_config_get",
            "gateway_network_config_update",
            "gateway_connector_config_update",
            "gateway_swaps_status",
            "gateway_swaps_search",
            "gateway_swaps_summary",
        ):
            self.assertIn(name, definitions)

    def test_gateway_network_config_update_schema(self):
        definitions = {tool["name"]: tool for tool in tool_definitions()}
        schema = definitions["gateway_network_config_update"]["inputSchema"]["properties"]

        self.assertIn("network_id", schema)
        self.assertIn("config_updates", schema)

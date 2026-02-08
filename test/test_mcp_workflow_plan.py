import unittest

try:
    from mcp.workflows import build_deploy_v2_workflow_plan
except ModuleNotFoundError:
    build_deploy_v2_workflow_plan = None


class _FakeClient:
    def __init__(self):
        self.called_instances = False

    def get(self, path, params=None):
        if path == "/gateway/status":
            return {"running": True}
        if path == "/bot-orchestration/instances":
            self.called_instances = True
            return {"data": {"instances": []}}
        return {}

    def post(self, path, params=None, json_body=None):
        if path == "/gateway/allowances":
            # Gateway returns `approvals` (token -> allowance string in token units).
            return {"spender": "0xspender", "approvals": {"USDT": "0"}}
        return {}

    def delete(self, path, params=None):
        return {}


class McpWorkflowPlanTests(unittest.TestCase):
    @unittest.skipIf(build_deploy_v2_workflow_plan is None, "mcp dependencies not installed")
    def test_plan_notes_apply_gateway_defaults_disabled(self):
        client = _FakeClient()
        plan = build_deploy_v2_workflow_plan(
            {
                "deployment_type": "script",
                "instance_name": "bot",
                "gateway_network_id": "ethereum-mainnet",
                "apply_gateway_defaults": False,
            },
            client,
        )

        self.assertTrue(any("apply_gateway_defaults=false" in note for note in plan["notes"]))

    @unittest.skipIf(build_deploy_v2_workflow_plan is None, "mcp dependencies not installed")
    def test_plan_notes_apply_gateway_defaults_enabled(self):
        client = _FakeClient()
        plan = build_deploy_v2_workflow_plan(
            {
                "deployment_type": "script",
                "instance_name": "bot",
                "gateway_network_id": "ethereum-mainnet",
                "apply_gateway_defaults": True,
            },
            client,
        )

        self.assertTrue(any("Gateway defaultNetwork/defaultWallet" in note for note in plan["notes"]))

    @unittest.skipIf(build_deploy_v2_workflow_plan is None, "mcp dependencies not installed")
    def test_plan_skips_instance_check_when_unique_name_generated(self):
        client = _FakeClient()
        plan = build_deploy_v2_workflow_plan(
            {
                "deployment_type": "controllers",
                "instance_name": "bot",
                "unique_instance_name": True,
            },
            client,
        )

        self.assertTrue(any("uniquified" in note for note in plan["notes"]))
        self.assertFalse(client.called_instances)

    @unittest.skipIf(build_deploy_v2_workflow_plan is None, "mcp dependencies not installed")
    def test_plan_adds_gateway_approve_action_when_allowance_missing(self):
        client = _FakeClient()
        plan = build_deploy_v2_workflow_plan(
            {
                "network_id": "ethereum-bsc",
                "wallet_address": "0xwallet",
                "spender": "pancakeswap/router",
                "tokens": [{"symbol": "USDT"}],
            },
            client,
        )

        self.assertTrue(any(action.get("tool") == "gateway_approve" for action in plan["actions"]))

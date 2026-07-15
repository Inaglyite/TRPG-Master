import tempfile
import unittest
from pathlib import Path

from src.config import PROJECT_ROOT
from src.runtime import RuntimeContext
from src.tool_runtime import DuplicateToolError, ToolRuntime, UnknownToolError


class ToolRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.context = RuntimeContext(
            project_root=PROJECT_ROOT,
            runtime_root=Path(self.temp_dir.name),
            world_id="tool-runtime-test",
            module_name="mansion_of_madness",
        )

    def test_registration_execution_and_audit(self):
        runtime = ToolRuntime()

        @runtime.handler("echo")
        def echo(args, context):
            return f"{context.world_id}:{args['value']}"

        result = runtime.execute("echo", {"value": "ok"}, self.context)

        self.assertEqual("tool-runtime-test:ok", result)
        record = runtime.audit_snapshot()[0]
        self.assertTrue(record.ok)
        self.assertEqual("echo", record.name)

    def test_duplicate_and_unknown_tools_fail_explicitly(self):
        runtime = ToolRuntime()
        runtime.add("echo", lambda _args, _context: "ok")

        with self.assertRaises(DuplicateToolError):
            runtime.add("echo", lambda _args, _context: "other")
        with self.assertRaises(UnknownToolError):
            runtime.execute("missing", {}, self.context)

    def test_handler_failure_is_audited_and_re_raised(self):
        runtime = ToolRuntime()

        def fail(_args, _context):
            raise RuntimeError("boom")

        runtime.add("fail", fail)
        with self.assertRaisesRegex(RuntimeError, "boom"):
            runtime.execute("fail", {}, self.context)

        record = runtime.audit_snapshot()[0]
        self.assertFalse(record.ok)
        self.assertEqual("RuntimeError", record.error_type)


class BuiltinToolContractTests(unittest.TestCase):
    def test_every_declared_model_tool_has_exactly_one_runtime_handler(self):
        from src.tools import TOOL_RUNTIME, TOOLS

        declared = {tool["function"]["name"] for tool in TOOLS}

        self.assertEqual(set(), declared - TOOL_RUNTIME.names)

    def test_public_execute_function_preserves_unknown_tool_error_protocol(self):
        from src.tools import execute_function

        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext(
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
                world_id="unknown-tool-test",
                module_name="mansion_of_madness",
            )
            result = execute_function("not_installed", {}, context=context)

        self.assertEqual("[错误] 未知函数: not_installed", result)


if __name__ == "__main__":
    unittest.main()

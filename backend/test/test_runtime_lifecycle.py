"""异常清理与两个查询入口共用的失败策略回归。"""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.policies import should_repair_sql
from agent.runtime import ask_data_agent
from observability.tracer import current_trace, end_trace, start_trace


class RuntimeLifecycleTests(unittest.TestCase):
    def tearDown(self):
        end_trace(flush=False)

    def test_exception_releases_owned_trace(self):
        class BrokenGraph:
            def invoke(self, *args, **kwargs):
                raise RuntimeError("graph failed")
        end_trace(flush=False)
        with self.assertRaises(RuntimeError):
            ask_data_agent(BrokenGraph(), "问题", "session")
        self.assertIsNone(current_trace())

    def test_exception_preserves_caller_owned_trace(self):
        class BrokenGraph:
            def invoke(self, *args, **kwargs):
                raise RuntimeError("graph failed")
        trace = start_trace()
        with self.assertRaises(RuntimeError):
            ask_data_agent(BrokenGraph(), "问题", "session")
        self.assertIs(current_trace(), trace)

    def test_retry_policy(self):
        self.assertTrue(should_repair_sql({"attempts": 0}, 3))
        for state in ({"success": True}, {"llm_error": "模型不可用"},
                      {"last_error": "安全拦截：禁止写操作"}, {"attempts": 2}):
            self.assertFalse(should_repair_sql(state, 3))

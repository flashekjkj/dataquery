"""显式离线回归清单，避免 unittest 忽略普通函数测试。"""
import importlib
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from evaluation.prepare import ROOT


def main():
    modules=['test_collaboration','test_runtime_lifecycle','test_model_client',
             'test_sql_json','test_observability','test_evaluation']
    suite=unittest.TestSuite()
    for name in modules:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromName('test.'+name))
    graph_tests=importlib.import_module('test.test_graph')
    for name in sorted(dir(graph_tests)):
        if name.startswith('test_'):
            suite.addTest(unittest.FunctionTestCase(getattr(graph_tests,name)))
    ROOT.mkdir(exist_ok=True)
    with (ROOT/'offline-tests.log').open('w',encoding='utf-8') as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    summary={'run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
             'skipped':len(result.skipped),'modules':modules,'function_module':'test_graph',
             'ok':result.wasSuccessful()}
    (ROOT/'offline-tests.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(summary)
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':
    raise SystemExit(main())

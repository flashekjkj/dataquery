import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from evaluation.comparison import assess,evidence_tables,fatal_provider_error


class ComparisonTests(unittest.TestCase):
    def test_account_failure_is_fatal_but_transient_timeout_is_not(self):
        self.assertTrue(fatal_provider_error(Exception('Arrearage overdue-payment')))
        self.assertFalse(fatal_provider_error(Exception('request timed out')))
    def case(self):
        return {'group':'compound','expected_results':[[[18]],[['甲',100],['乙',90]]]}
    def test_single_json_and_multi_tables_have_equal_scores(self):
        single={'success':True,'query_result':{'columns':['json'], 'rows':[[json.dumps({'count':18,'ranking':[{'name':'甲','amount':100},{'name':'乙','amount':90}]},ensure_ascii=False)]],'is_truncated':False}}
        multi={'success':True,'task_results':[{'status':'approved','query_result':{'rows':[[18]],'is_truncated':False}},
                                            {'status':'approved','query_result':{'rows':[['甲',100],['乙',90]],'is_truncated':False}}]}
        self.assertEqual(assess(self.case(),single)['fact_flags'],assess(self.case(),multi)['fact_flags'])
        self.assertTrue(assess(self.case(),single)['strict'])
    def test_tagged_union_is_accepted(self):
        single={'success':True,'query_result':{'columns':['result_type','name','value'],'rows':[['count',None,18],['top','甲',100],['top','乙',90]],'is_truncated':False}}
        self.assertTrue(assess(self.case(),single)['strict'])
    def test_partial_credit_does_not_require_overall_success(self):
        result={'success':False,'task_results':[{'status':'approved','query_result':{'rows':[[18]],'is_truncated':False}}]}
        score=assess(self.case(),result)
        self.assertFalse(score['strict'])
        self.assertEqual(score['coverage'],.5)
    def test_unapproved_or_truncated_evidence_not_accepted(self):
        result={'success':True,'task_results':[{'status':'unverified','query_result':{'rows':[[18]],'is_truncated':False}}]}
        self.assertEqual(assess(self.case(),result)['coverage'],0)
    def test_clarification_not_tied_to_multi_status(self):
        case={'group':'ambiguous'}
        self.assertTrue(assess(case,{'answer':'请提供销售目标和时间范围，才能判断是否达标。'})['strict'])
        self.assertFalse(assess(case,{'answer':'已经达标。'})['strict'])


if __name__=='__main__':
    unittest.main()

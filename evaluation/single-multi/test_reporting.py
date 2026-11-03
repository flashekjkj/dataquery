import importlib.util
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location('report_comparison',Path(__file__).with_name('report_comparison.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class ReportingTests(unittest.TestCase):
    def test_partitioned_rows_equal_one_table(self):
        self.assertTrue(m.partition_match([[9,100.0],[10,80.0]],[{'rows':[[9,100.0]]},{'rows':[[10,80.0]]}]))
    def test_extra_rows_not_silently_selected(self):
        self.assertFalse(m.partition_match([[9,100.0]],[{'rows':[[9,100.0],[10,80.0]]}]))
    def test_currency_two_decimals(self):
        self.assertTrue(m.partition_match([[16.708333]],[{'rows':[[16.71]]}]))
    def test_ratio_four_decimals(self):
        self.assertTrue(m.partition_match([['华东',.13103448]],[{'rows':[['华东',.131]]}],ratio=True))
        self.assertFalse(m.partition_match([['华东',.13103448]],[{'rows':[['华东',.13]]}],ratio=True))
    def test_counts_cannot_be_rounded(self):
        self.assertFalse(m.partition_match([[18]],[{'rows':[[18.001]]}]))
    def test_metric_long_form_and_wide_form_equal(self):
        expected=[['普通客户',6,100.25,16.708333]]
        result={'query_result':{'columns':['level','month','metric_name','metric_value'],
                               'rows':[['普通客户',10,'人数',6],['普通客户',10,'总额',100.25],['普通客户',10,'人均',16.71]],
                               'is_truncated':False}}
        self.assertTrue(m.partition_match(expected,m.expanded_evidence(result)))


if __name__=='__main__':
    unittest.main()

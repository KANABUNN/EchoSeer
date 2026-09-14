"""Qt-free classification fixtures used by GUI and pipeline tests."""
from audio.operations import check_cancel
from tests.unit.test_sequence_analyzer import StubClassifier
from tests.unit.test_pass_comparator import event,passes
from encounter.vog_oracles import OracleId


class RowsClassifier(StubClassifier):
    def __init__(self,rows):
        super().__init__()
        self.rows=tuple(entry.classification for entry in rows)
        self.cursor=0
    def classify_preprocessed(self,clip,cancel=None):
        check_cancel(cancel)
        raw=self.rows[self.cursor%len(self.rows)]
        self.cursor+=1
        return raw


def rows_for(case):
    first,second=passes([OracleId.L1,OracleId.L2,OracleId.L3])
    first,second=list(first),list(second)
    if case=="INFERRED":
        first[2]=event(OracleId.L2,.83,{OracleId.L3:.82},start=first[2].timestamp)
    elif case=="MISMATCH":
        second[2]=event(OracleId.R3,.99,start=second[2].timestamp)
    elif case=="CHECK":
        for rows in (first,second):
            rows[2]=event(OracleId.L3,.9,{OracleId.R3:.9},start=rows[2].timestamp)
    return [*first,*second]

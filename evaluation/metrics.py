"""Deterministic order/time alignment; rejected and missing events are separate."""
from dataclasses import dataclass
from config.defaults import ORACLE_KEYS

ACTUAL_KEYS=(*ORACLE_KEYS,"NO_ORACLE")
PREDICTED_KEYS=(*ORACLE_KEYS,"UNKNOWN","MISSING")


@dataclass(frozen=True, slots=True)
class Prediction:
    oracle: str | None
    onset: float
    end: float


def align(expected, predictions, timestamps=None, tolerance=.15):
    n,m=len(expected),len(predictions)
    cells=[[None]*(m+1) for _ in range(n+1)]
    back=[[None]*(m+1) for _ in range(n+1)]
    timed=timestamps is not None
    cells[0][0]=(0,0.0) if timed else 0
    for i in range(1,n+1):
        cells[i][0]=cells[i-1][0] if timed else i; back[i][0]="miss"
    for j in range(1,m+1):
        cells[0][j]=cells[0][j-1] if timed else j; back[0][j]="extra"
    for i in range(1,n+1):
        for j in range(1,m+1):
            if timed:
                options=[(cells[i-1][j],"miss"),(cells[i][j-1],"extra")]
                start,end=timestamps[i-1]
                if start-tolerance <= predictions[j-1].onset <= end+tolerance:
                    score=cells[i-1][j-1]
                    options.insert(0,((score[0]+1,score[1]-abs(predictions[j-1].onset-start)),"pair"))
                cells[i][j],back[i][j]=max(options,key=lambda item:item[0])
            else:
                options=[(cells[i-1][j-1]+(expected[i-1]!=predictions[j-1].oracle),"pair"),
                         (cells[i-1][j]+1,"miss"),(cells[i][j-1]+1,"extra")]
                cells[i][j],back[i][j]=min(options,key=lambda item:item[0])
    pairs=[]; i,j=n,m
    while i or j:
        action=back[i][j]
        if action=="pair":
            pairs.append((i-1,j-1)); i-=1; j-=1
        elif action=="miss":
            pairs.append((i-1,None)); i-=1
        else:
            pairs.append((None,j-1)); j-=1
    return tuple(reversed(pairs))


def ratio(numerator,denominator):
    return numerator/denominator if denominator else None


def score_case(expected,predictions,timestamps=None,tolerance=.15):
    matrix={a:{p:0 for p in PREDICTED_KEYS} for a in ACTUAL_KEYS}
    counts={"expected":len(expected),"detected":len(predictions),"correct":0,"incorrect":0,
            "rejected":0,"missed":0,"false_positives":0,"extra_unknown":0,
            "rejected_predictions":sum(p.oracle is None for p in predictions)}
    outcomes=[]
    for ei,pi in align(expected,predictions,timestamps,tolerance):
        actual=expected[ei] if ei is not None else "NO_ORACLE"
        predicted=(predictions[pi].oracle or "UNKNOWN") if pi is not None else "MISSING"
        matrix[actual][predicted]+=1
        if ei is None:
            outcome="false_positives" if predicted!="UNKNOWN" else "extra_unknown"
        elif pi is None:
            outcome="missed"
        elif predicted=="UNKNOWN":
            outcome="rejected"
        else:
            outcome="correct" if actual==predicted else "incorrect"
        counts[outcome]+=1
        outcomes.append({"expected_index":ei+1 if ei is not None else None,
                         "prediction_index":pi+1 if pi is not None else None,"outcome":outcome})
    return {"counts":counts,"confusion_matrix":matrix,"alignment":outcomes,
            "case_correct":counts["correct"]==len(expected) and not counts["false_positives"]}

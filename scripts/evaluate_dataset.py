"""Evaluate labelled WAVs and compare profiles without changing installed configuration."""
import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from app.paths import AppPaths
from config.schema import AppConfig
from detector.classifier import OracleClassifier
from dsp.bandpass import BandpassSettings
from evaluation.dataset import load_dataset,read_json
from evaluation.runner import MAX_REPORT_BYTES,compare_reports,evaluate,export_report
from templates.manager import TemplateManager


def main(argv=None):
    parser=argparse.ArgumentParser(description="Oracle Dataset evaluation")
    parser.add_argument("--dataset",required=True,type=Path)
    parser.add_argument("--templates",type=Path)
    parser.add_argument("--config",type=Path)
    parser.add_argument("--baseline",type=Path)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args(argv)
    settings=AppConfig.from_dict(read_json(args.config)) if args.config else AppConfig()
    r=settings.recognition
    manager=TemplateManager(args.templates or AppPaths.discover().templates,settings.audio.internal_sample_rate)
    classifier=OracleClassifier(manager,settings.audio.internal_sample_rate,r.template_aggregation,r.top_n,
        BandpassSettings(r.bandpass_low_hz,r.bandpass_high_hz) if r.bandpass_enabled else None,
        waveform_weight=r.waveform_weight,spectrum_weight=r.spectrum_weight)
    report=evaluate(load_dataset(args.dataset),classifier,r,sequence=settings.sequence)
    if args.baseline:
        report["comparison"]=compare_reports(report,read_json(args.baseline,MAX_REPORT_BYTES))
    output=args.output or ROOT/".runtime"/"dataset-evaluation"/uuid4().hex
    path=export_report(report,output)
    print(json.dumps({"report":str(path),"metrics":report["metrics"]},ensure_ascii=False,allow_nan=False))
    return 0


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except (OSError,ValueError) as error:
        print(f"Evaluation failed: {error}",file=sys.stderr)
        raise SystemExit(1)

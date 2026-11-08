"""Read-only inspection by default. Repair requires an explicit row and justification."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diffusion_policy.common.gello_zarr import inspect_dataset, repair_action_row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset')
    parser.add_argument('--check-chunks', action='store_true')
    parser.add_argument('--json-output')
    parser.add_argument('--drop-action-index', type=int)
    parser.add_argument('--output')
    parser.add_argument('--reason', default='')
    parser.add_argument('--episode-ends', help='Verified comma-separated episode boundaries for the repaired copy.')
    args = parser.parse_args()
    if args.drop_action_index is not None:
        if not args.output or not args.reason.strip():
            parser.error('Repair requires --output and a verified alignment --reason.')
        ends = [int(x) for x in args.episode_ends.split(',')] if args.episode_ends else None
        repair_action_row(args.dataset, args.output, args.drop_action_index, args.reason, ends)
    elif args.output or args.reason or args.episode_ends:
        parser.error('--output/--reason are only used with --drop-action-index.')
    report = inspect_dataset(args.output or args.dataset, check_chunks=args.check_chunks)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.json_output:
        Path(args.json_output).write_text(rendered, encoding='utf-8')
    return 0 if report['valid'] else 2


if __name__ == '__main__':
    raise SystemExit(main())

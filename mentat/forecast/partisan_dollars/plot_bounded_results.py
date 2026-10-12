"""Render a saved, verified interval experiment; never calculate new forecasts."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evaluation', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing to overwrite an existing figure')
    result = json.loads(args.evaluation.read_text(encoding='utf-8'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    horizons = ('m12', 'm15', 'm18', 'm21')
    names = {'prior_party': 'Repeat prior cycle', 'party_R12_remaining': 'Reported giving + estimated remainder',
             'historical_remainder': 'Reported giving + typical past remainder'}
    colors = {'prior_party': '#829099', 'party_R12_remaining': '#3054BB', 'historical_remainder': '#B86B21'}
    selected = {(r['chamber'], r['horizon'], r['family']): r for r in result['metrics']
                if r['sector'] == r['party'] == r['target'] == 'ALL' and r['chamber'] != 'ALL'}
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), sharex=True, sharey=True)
    fig.patch.set_facecolor('#FAFAF6')
    for col, chamber in enumerate(('House', 'Senate')):
        for row, band in enumerate((10, 20)):
            ax = axes[row, col]
            ax.set_facecolor('#FAFAF6')
            for family in names:
                points = [selected[chamber, h, family] for h in horizons]
                if any(p['n'] != 40 for p in points):
                    raise ValueError('Expected all forty forecasts per chamber/horizon/family')
                values = [p[f'certified_within{band}_fraction_all_cells'] for p in points]
                ax.plot(range(4), values, marker='o', linewidth=2.4, color=colors[family], label=names[family])
            ax.set_title(f'{chamber} · within {band}% of actual dollars', loc='left', fontsize=13, weight='bold')
            ax.yaxis.set_major_formatter(PercentFormatter(1))
            ax.set_ylim(-.025, 1.025)
            ax.set_yticks([0, .25, .5, .75, 1])
            ax.grid(axis='y', color='#DEE1DD', linewidth=.8)
            ax.spines[['top', 'right', 'left']].set_visible(False)
            ax.spines['bottom'].set_color('#C5CAC5')
            ax.tick_params(axis='both', length=0, labelsize=10, pad=8)
            ax.set_xticks(range(4), ['January', 'April', 'July', 'October'])
            if col == 0:
                ax.set_ylabel('Share of forecasts certified', fontsize=10)
    fig.suptitle('How much does reported giving help predict partisan dollars?',
                 x=.07, y=.98, ha='left', fontsize=20, weight='bold', color='#1F292F')
    fig.text(.07, .925, 'Five selected PAC cohorts · D/R separately · 2018–2024 reconstructed tests · 40 forecasts per panel/date',
             fontsize=11, color='#506069')
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='lower left', bbox_to_anchor=(.06, .057), ncol=3, frameon=False, fontsize=10)
    fig.text(.07, .024, 'Certification requires the full uncertainty range to fit the tolerance. Uncertified forecasts are not necessarily misses.\n'
             'Canonical cycle-office accounting; later amended records and fixed cohorts. These are retrospective results, not a future accuracy guarantee.',
             fontsize=9, color='#506069', va='bottom')
    fig.subplots_adjust(left=.07, right=.98, top=.86, bottom=.19, hspace=.29, wspace=.13)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=180, facecolor=fig.get_facecolor())
    plt.close(fig)


if __name__ == '__main__':
    main()

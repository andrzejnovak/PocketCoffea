import os
import sys

import numpy as np
import matplotlib
import matplotlib.pyplot as plt

import mplhep as hep
import hist
from coffea import processor
from coffea.hist import Bin, Hist, plot
from coffea.lookup_tools.dense_lookup import dense_lookup
from coffea.util import save, load

import correctionlib, rich
import correctionlib.convert

from pocket_coffea.parameters.lumi import lumi, femtobarn

# color_datamc = {'data' : 'black', 'mc' : 'red'}
opts_data = {
    'linestyle': 'solid',
    'linewidth': 0,
    'marker': '.',
    'markersize': 1.0,
    'color': 'black',
    'elinewidth': 1,
    'label': 'Data',
}

opts_mc = {
    'nominal': {
        'linestyle': 'solid',
        'linewidth': 0,
        'marker': '.',
        'markersize': 1.0,
        'color': 'red',
        'elinewidth': 1,
        'label': r'$t\bar{t}$ DL + SL MC',
    },
    'Up': {
        'linestyle': 'dashed',
        'linewidth': 0,
        'marker': '.',
        'markersize': 1.0,
        'color': 'red',
        'elinewidth': 1,
        'label': r'$t\bar{t}$ DL + SL MC',
    },
    'Down': {
        'linestyle': 'dotted',
        'linewidth': 0,
        'marker': '.',
        'markersize': 1.0,
        'color': 'red',
        'elinewidth': 1,
        'label': r'$t\bar{t}$ DL + SL MC',
    },
}

opts_sf = {
    'nominal': {
        'linestyle': 'solid',
        'linewidth': 0,
        'marker': '.',
        'markersize': 1.0,
        'color': 'red',
        'elinewidth': 1,
    },
    'Up': {
        'linestyle': 'dashed',
        'linewidth': 0,
        'marker': '.',
        'markersize': 1.0,
        'color': 'red',
        'elinewidth': 1,
    },
    'Down': {
        'linestyle': 'dotted',
        'linewidth': 0,
        'marker': '.',
        'markersize': 1.0,
        'color': 'red',
        'elinewidth': 1,
    },
}

opts_errorbar = {
    'nominal': {'linestyle': 'solid'},
    'Up': {'linestyle': 'dashed'},
    'Down': {'linestyle': 'dotted'},
}

hatch_density = 4
opts_unc = {
    "step": "post",
    "color": (0, 0, 0, 0.4),
    "facecolor": (0, 0, 0, 0.0),
    "linewidth": 0,
    "hatch": '/' * hatch_density,
    "zorder": 2,
    "label": "Stat. unc.",
}

patch_opts = {
    'data': {'vmin': 0.4, 'vmax': 1.0},
    'mc': {'vmin': 0.4, 'vmax': 1.0},
    'sf': {'vmin': 0.75, 'vmax': 1.2, 'label': "Trigger SF"},
    'unc_data': {'vmax': 0.05},
    'unc_mc': {'vmax': 0.05},
    'unc_sf': {'vmax': 0.05, 'label': "Trigger SF unc."},
    'unc_rel_data': {'vmax': 0.05},
    'unc_rel_mc': {'vmax': 0.05},
    'unc_rel_sf': {'vmax': 0.05, 'label': "Trigger SF unc."},
    'ratio_sf': {'vmin': 0.95, 'vmax': 1.05, 'label': "ratio SF var./nom."},
}

round_opts = {
    'data': {'round': 2},
    'mc': {'round': 2},
    'sf': {'round': 2},
    'unc_data': {'round': 2},
    'unc_mc': {'round': 2},
    'unc_sf': {'round': 2},
    'unc_rel_data': {'round': 2},
    'unc_rel_mc': {'round': 2},
    'unc_rel_sf': {'round': 2},
    'ratio_sf': {'round': 3},
}

# def plot_variation(x, y, yerr, xerr, xlabel, ylabel, syst, var, opts, ax, data=False, sf=False, **kwargs):
def plot_variation_correctionlib(file, axis_x, systematics, plot_dir, **kwargs):
    config = kwargs['config']
    cset = correctionlib.CorrectionSet.from_file(file)
    if len(list(cset.keys())) > 1:
        sys.exit("Choice of the correction key is ambiguous.")
    elif len(list(cset.keys())) == 1:
        key = list(cset.keys())[0]
    correction = cset[key]
    edges_x = axis_x.edges
    xlabel = axis_x.label
    ylabel = "Trigger SF"
    variable = kwargs['histname'].split('hist_')[-1]
    totalLumi = femtobarn(lumi[kwargs['year']]['tot'], digits=1)
    systematics = ['nominal'] + [s.split('Up')[0] for s in systematics if 'Up' in s]

    for syst in systematics:

        if syst == 'nominal':
            fig, ax = plt.subplots(1, 1, figsize=[10, 10])
            plt.subplots_adjust(wspace=0.3)
        else:
            fig, (ax, rax) = plt.subplots(
                2,
                1,
                figsize=[10, 10],
                gridspec_kw={"height_ratios": (3, 1)},
                sharex=True,
            )
        hep.cms.text("Preliminary", loc=0, ax=ax)
        hep.cms.lumitext(
            text=f'{totalLumi}' + r' fb$^{-1}$, 13 TeV,' + f' {kwargs["year"]}',
            fontsize=18,
            ax=ax,
        )
        binwidth_x = np.ediff1d(edges_x)
        x = edges_x[:-1] + 0.5 * binwidth_x
        xerr = 0.5 * binwidth_x
        nominal = correction.evaluate("nominal", x)
        statDown = correction.evaluate("statDown", x)
        statUp = correction.evaluate("statUp", x)
        yerr = np.array([abs(nominal - statDown), abs(statUp - nominal)])
        ax.errorbar(
            x, nominal, yerr=yerr, xerr=xerr, label="SF nominal", **opts_sf['nominal']
        )
        xlim = (edges_x[0], edges_x[-1])
        xticks = None
        if (
            kwargs['histname']
            in config.plot_options['scalefactor'][kwargs['year']][kwargs['cat']].keys()
        ):
            ylim = config.plot_options['scalefactor'][kwargs['year']][kwargs['cat']][
                kwargs['histname']
            ]['ylim']
        else:
            ylim = (0.7, 1.3)
        if (
            kwargs['histname']
            in config.plot_options['ratio'][kwargs['year']][kwargs['cat']].keys()
        ):
            ylim_ratio = config.plot_options['ratio'][kwargs['year']][kwargs['cat']][
                kwargs['histname']
            ]['ylim']
        else:
            ylim_ratio = (0.90, 1.10)
        if variable in config.plot_options['rebin'].keys():
            if 'xticks' in config.plot_options['rebin'][variable].keys():
                xticks = config.plot_options['rebin'][variable]['xticks']
            if 'xlim' in config.plot_options['rebin'][variable].keys():
                xlim = config.plot_options['rebin'][variable]['xlim']
        if syst != 'nominal':
            systDown = correction.evaluate(f"{syst}Down", x)
            systUp = correction.evaluate(f"{syst}Up", x)
            ratioDown = systDown / nominal
            ratioUp = systUp / nominal
            unc_nominalUp = abs(statUp - nominal)
            unc_nominalDown = abs(nominal - statDown)
            unc_ratioUp = (unc_nominalUp / nominal) * ratioUp
            unc_ratioDown = (unc_nominalDown / nominal) * ratioDown
            lo = 1 - unc_ratioDown
            hi = 1 + unc_ratioUp
            unc_band = np.nan_to_num(np.array([lo, hi]), nan=1)

            linesDown = ax.errorbar(
                x,
                systDown,
                yerr=0,
                xerr=xerr,
                label=f"SF {syst}Down",
                **opts_sf['Down'],
            )
            linesUp = ax.errorbar(
                x, systUp, yerr=0, xerr=xerr, label=f"SF {syst}Up", **opts_sf['Up']
            )
            rlinesDown = rax.errorbar(
                x, ratioDown, yerr=0, xerr=xerr, **opts_sf['Down']
            )
            rlinesUp = rax.errorbar(x, ratioUp, yerr=0, xerr=xerr, **opts_sf['Up'])
            for lines, var in zip(
                [linesDown, linesUp, rlinesDown, rlinesUp], ['Down', 'Up', 'Down', 'Up']
            ):
                errorbar_x = lines[-1][0]
                errorbar_y = lines[-1][1]
                errorbar_x.set_linestyle(opts_errorbar[var]['linestyle'])
                errorbar_y.set_linewidth(0)
            rax.fill_between(
                edges_x,
                np.r_[unc_band[0], unc_band[0, -1]],
                np.r_[unc_band[1], unc_band[1, -1]],
                **opts_unc,
            )
            rax.set_xlabel(xlabel, fontsize=kwargs['fontsize'])
            rax.set_ylabel("SF var./nom.", fontsize=kwargs['fontsize'])
            rax.set_ylim(*ylim_ratio)
            if variable == 'electron_pt':
                rax.set_xscale('log')
            if xticks:
                rax.set_xticks(xticks)
            if rax.get_xaxis().get_scale() == 'log':
                rax.get_xaxis().set_major_formatter(matplotlib.ticker.ScalarFormatter())
            rax.legend()
        else:
            ax.set_xlabel(xlabel, fontsize=kwargs['fontsize'])
            if variable == 'electron_pt':
                ax.set_xscale('log')
            if xticks:
                ax.set_xticks(xticks)
            if ax.get_xaxis().get_scale() == 'log':
                ax.get_xaxis().set_major_formatter(matplotlib.ticker.ScalarFormatter())

        ax.legend()
        ax.set_ylabel(ylabel, fontsize=kwargs['fontsize'])
        ax.hlines(1.0, *xlim, linestyle='dashed', color='gray')
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        filename = os.path.join(
            plot_dir,
            f"{kwargs['histname']}_{kwargs['year']}_sf_{kwargs['cat']}_{syst}.png",
        )
        print("Saving", filename)
        fig.savefig(filename, dpi=config.plot_options['dpi'], format="png")

import numpy as np
import awkward as ak
from collections import defaultdict
from abc import ABC, abstractmethod

import copy
import logging

from coffea import processor
from coffea.lumi_tools import LumiMask
from coffea.analysis_tools import PackedSelection

from ..lib.weights_manager import WeightsManager
from ..lib.columns_manager import ColumnsManager
from ..lib.hist_manager import HistManager
from ..parameters.event_flags import event_flags, event_flags_data
from ..parameters.lumi import goldenJSON
from ..parameters.btag import btag

from ..utils.configurator import Configurator


class BaseProcessorABC(processor.ProcessorABC, ABC):
    '''
    Abstract Class which defined the common operations of a PocketCoffea
    processor.
    '''

    def __init__(self, cfg: Configurator) -> None:
        '''
        Constructor of the BaseProcessor.
        It instanties the objects needed for skimming and categories and prepared the
        `output_format` dictionary.

        :param cfg: Configurator object containing all the configurations.
        '''
        # Read required cuts and histograms from config file
        self.cfg = cfg

        # Cuts
        # The definition of the cuts in the analysis is hierarchical.
        # 1) a list of *skim* functions is applied on bare NanoAOD, with no object preselection or correction.
        #   Triggers are also applied there.
        # 2) Objects are corrected and selected and a list of *preselection* cuts are applied to skim the dataset.
        # 3) A list of cut function is applied and the masks are kept in memory to defined later "categories"
        self._skim = self.cfg.skim
        self._preselections = self.cfg.preselections
        self._cuts = self.cfg.cut_functions
        self._categories = self.cfg.categories
        # Define PackedSelector to save per-event cuts and dictionary of selections
        # The skim mask is applied on baseline nanoaod before any object is corrected
        self._skim_masks = PackedSelection()
        # The preselection mask is applied after the objects have been corrected
        self._preselection_masks = PackedSelection()
        # After the preselections more cuts are defined and combined in categories.
        # These cuts are applied only for outputs, so they cohexists in the form of masks
        self._cuts_masks = PackedSelection()

        # Subsamples configurations: special cuts to split a sample in subsamples
        self._subsamplesCfg = self.cfg.subsamples

        # List of all samples names
        self._totalSamplesSet = self.cfg.samples[:]
        for sample, subs in self._subsamplesCfg.items():
            self._totalSamplesSet += list(subs.keys())
        logging.debug(f"Total samples list: {self._totalSamplesSet}")

        # Weights configuration
        self.weights_config_allsamples = self.cfg.weights_config

        # Custom axis for the histograms
        self.custom_axes = []
        self.custom_histogram_fields = {}

        # Output format
        # Accumulators for the output
        self.output_format = {
            "sum_genweights": {},
            "sumw": {
                cat: {s: 0.0 for s in self.cfg.samples} for cat in self._categories
            },
            "cutflow": {
                "initial": {s: 0 for s in self.cfg.samples},
                "skim": {s: 0 for s in self.cfg.samples},
                "presel": {s: 0 for s in self.cfg.samples},
                **{cat: {s: 0.0 for s in self.cfg.samples} for cat in self._categories},
            },
            "seed_chunk": defaultdict(str),
            "variables": {
                v: {}
                for v, vcfg in self.cfg.variables.items()
                if not vcfg.metadata_hist
            },
            "columns": {},
            "processing_metadata": {
                v: {} for v, vcfg in self.cfg.variables.items() if vcfg.metadata_hist
            },
        }

    @property
    def nevents(self):
        '''Compute the current number of events in the current chunk.
        If the function is called after skimming or preselection the
        number of events is reduced accordingly'''
        return len(self.events)

    def load_metadata(self):
        '''
        The function is called at the beginning of the processing for each chunk
        to load some metadata depending on the chunk sample, year and dataset.
        '''
        self._dataset = self.events.metadata["dataset"]
        self._sample = self.events.metadata["sample"]
        self._year = self.events.metadata["year"]
        self._btag = btag[self._year]
        self._isMC = self.events.metadata["isMC"] == "True"
        if self._isMC:
            self._era = "MC"
        else:
            self._era = self.events.metadata["era"]
            self._goldenJSON = goldenJSON[self._year]
        # Loading metadata for subsamples
        self._hasSubsamples = self._sample in self._subsamplesCfg
        if self._hasSubsamples:
            self._subsamples = self._subsamplesCfg[self._sample]
            self._subsamples_names = self._subsamples.keys()

    def load_metadata_extra(self):
        '''
        Function that can be called by a derived processor to define additional metadata.
        For example load additional information for a specific sample.
        '''
        pass

    def skim_events(self):
        '''
        Function which applied the initial event skimming.
        By default the skimming comprehend:

          - METfilters,
          - PV requirement *at least 1 good primary vertex
          - lumi-mask (for DATA): applied the goldenJson selection
          - requested HLT triggers (from configuration, not hardcoded in the processor)
          - **user-defined** skimming cuts

        BE CAREFUL: the skimming is done before any object preselection and cleaning.
        Only collections and branches already present in the NanoAOD before any correct
        can be used.
        Alternatively, if you need to apply the cut on preselected objects,
        defined the cut at the preselection level, not at skim level.
        '''
        mask_flags = np.ones(self.nEvents_initial, dtype=np.bool)
        flags = event_flags[self._year]
        if not self._isMC:
            flags += event_flags_data[self._year]
        for flag in flags:
            mask_flags = getattr(self.events.Flag, flag)
        self._skim_masks.add("event_flags", mask_flags)

        # Primary vertex requirement
        self._skim_masks.add("PVgood", self.events.PV.npvsGood > 0)

        # In case of data: check if event is in golden lumi file
        if not self._isMC:
            # mask_lumi = lumimask(self.events.run, self.events.luminosityBlock)
            mask_lumi = LumiMask(self._goldenJSON)(
                self.events.run, self.events.luminosityBlock
            )
            self._skim_masks.add("lumi_golden", mask_lumi)

        for skim_func in self._skim:
            # Apply the skim function and add it to the mask
            mask = skim_func.get_mask(
                self.events,
                year=self._year,
                sample=self._sample,
                btag=self._btag,
                isMC=self._isMC,
            )
            self._skim_masks.add(skim_func.id, mask)

        # Finally we skim the events and count them
        self.events = self.events[self._skim_masks.all(*self._skim_masks.names)]
        self.nEvents_after_skim = self.nevents
        self.output['cutflow']['skim'][self._sample] += self.nEvents_after_skim
        self.has_events = self.nEvents_after_skim > 0

    @abstractmethod
    def apply_object_preselection(self):
        '''
        Function which **must** be defined by the actual user processor to preselect
        and clean objects and define the collections as attributes of `events`.
        E.g.::

             self.events["ElectronGood"] = lepton_selection(self.events, "Electron", self.cfg.finalstate)
        '''
        pass

    @abstractmethod
    def count_objects(self):
        '''
        Function that counts the preselected objects and save the counts as attributes of `events`.
        The function **must** be defined by the user processor.
        '''
        pass

    def apply_preselections(self):
        '''The function computes all the masks from the preselection cuts
        and filter out the events to speed up the later computations.
        N.B.: Preselection happens after the objects correction and cleaning.'''
        for cut in self._preselections:
            # Apply the cut function and add it to the mask
            mask = cut.get_mask(
                self.events, year=self._year, sample=self._sample, isMC=self._isMC
            )
            self._preselection_masks.add(cut.id, mask)
        # Now that the preselection mask is complete we can apply it to events
        self.events = self.events[
            self._preselection_masks.all(*self._preselection_masks.names)
        ]
        self.nEvents_after_presel = self.nevents
        self.output['cutflow']['presel'][self._sample] += self.nEvents_after_presel
        self.has_events = self.nEvents_after_presel > 0

    def define_categories(self):
        '''
        The function saves all the cut masks internally, in order to use them later
        to define categories (groups of cuts.).

        Moreover it computes the cut masks defining the subsamples for the current
        chunks and store them in the `self.subsamples` attribute for later use.
        '''
        # We make sure that for each category the list of cuts is unique in the Configurator validation
        for cut in self._cuts:
            mask = cut.get_mask(
                self.events, year=self._year, sample=self._sample, isMC=self._isMC
            )
            self._cuts_masks.add(cut.id, mask)

        # Defining the subsamples cut if needed
        if self._hasSubsamples:
            # saving all the cuts in a single selector
            self._subsamples_masks = PackedSelection()
            self._subsamples_cuts_ids = []
            # saving the map of cut ids for each subsample
            self._subsamples_map = defaultdict(list)
            for subs, cuts in self._subsamples.items():
                for cut in cuts:
                    if cut.id not in self._subsamples_cuts_ids:
                        self._subsamples_masks.add(
                            cut.id,
                            cut.get_mask(
                                self.events,
                                year=self._year,
                                sample=self._sample,
                                isMC=self._isMC,
                            ),
                        )
                        self._subsamples_cuts_ids.append(cut.id)
                    self._subsamples_map[subs].append(cut.id)

    def define_common_variables_before_presel(self):
        '''
        Function that defines common variables employed in analyses
        and save them as attributes of `events`, **before preselection**.
        If the user processor does not redefine it, no common variables are built.
        '''
        pass

    def define_common_variables_after_presel(self):
        '''
        Function that defines common variables employed in analyses
        and save them as attributes of `events`,  **after preselection**.
        If the user processor does not redefine it, no common variables are built.
        '''
        pass

    @classmethod
    def available_weights(cls):
        '''
        Identifiers of the weights available thorugh this processor.
        By default they are all the weights defined in the WeightsManager
        '''
        return WeightsManager.available_weights()

    @classmethod
    def available_variations(cls):
        '''
        Identifiers of the weights variabtions available thorugh this processor.
        By default they are all the weights defined in the WeightsManager
        '''
        return WeightsManager.available_variations()

    def compute_weights(self):
        '''
        Function which define weights (called after preselection).
        The WeightsManager is build only for MC, not for data.
        The user processor can redefine this function to customize the
        weights computation object.
        '''
        if not self._isMC:
            self.weights_manager = None
        else:
            # Creating the WeightsManager with all the configured weights
            self.weights_manager = WeightsManager(
                self.weights_config_allsamples[self._sample],
                self.nEvents_after_presel,
                self.events,  # to compute weights
                storeIndividual=False,
                metadata={
                    "year": self._year,
                    "sample": self._sample,
                    "finalstate": self.cfg.finalstate,
                },
            )

    def compute_weights_extra(self):
        '''Function that can be defined by user processors
        to define **additional weights** to be added to the WeightsManager.
        To completely redefine the WeightsManager, use the function `compute_weights`
        '''
        pass

    def count_events(self):
        '''
        Count the number of events in each category and
        also sum their nominal weights (for each sample, by chunk).
        Store the results in the `cutflow` and `sumw` outputs
        '''
        for category, cuts in self._categories.items():
            mask = self._cuts_masks.all(*cuts)
            self.output["cutflow"][category][self._sample] = ak.sum(mask)
            if self._isMC:
                w = self.weights_manager.get_weight(category)
                self.output["sumw"][category][self._sample] = ak.sum(w * mask)

            # If subsamples are defined we also save their metadata
            if self._hasSubsamples:
                for subs in self._subsamples_names:
                    # Get the mask
                    subsam_mask = self._subsamples_masks.all(
                        *self._subsamples_map[subs]
                    )
                    mask_withsub = mask & subsam_mask
                    self.output["cutflow"][category][subs] = ak.sum(mask_withsub)
                    if self._isMC:
                        self.output["sumw"][category][subs] = ak.sum(w * mask_withsub)

    def define_custom_axes_extra(self):
        '''
        Function which get called before the definition of the Histogram
        manager.
        It is used to defined extra custom axes for the histograms
        depending on the current chunk metadata.
        E.g.: it can be used to add a `era` axes only for data.

        Custom axes needed for all the samples can be added in the
        user processor constructor, by appending to `self.custom_axes`.
        '''
        pass

    def define_histograms(self):
        '''
        Initialize the HistManager.
        Redefine to customize completely the creation of the histManager.

        If subsamples are defined, an histmanager is created for each of them.
        '''
        # Check if subsamples are requested
        self.hists_managers = {}
        if self._hasSubsamples:
            # in that case create a dictionary of histManagers
            for subs in self._subsamples_names:
                self.hists_managers[subs] = HistManager(
                    self.cfg.variables,
                    subs,
                    self._categories,
                    self.cfg.variations_config[self._sample],
                    custom_axes=self.custom_axes,
                    isMC=self._isMC,
                )
        else:
            self.hists_managers[self._sample] = HistManager(
                self.cfg.variables,
                self._sample,
                self._categories,
                self.cfg.variations_config[self._sample],
                custom_axes=self.custom_axes,
                isMC=self._isMC,
            )

    def define_histograms_extra(self):
        '''
        Function that get called after the creation of the HistManager.
        The user can redefine this function to manipulate the HistManager
        histogram configuration to add customizations directly to the histogram
        objects before the filling.

        This function should also be redefined to fill the `self.custom_histogram_fields`
        that are passed to the histogram filling.
        '''
        pass

    def fill_histograms(self):
        '''Function which fill the histograms for each category and variation,
        throught the HistManager.
        '''
        # Filling the autofill=True histogram automatically
        if self._hasSubsamples:
            # call the filling for each
            for subs in self._subsamples_names:
                # Get the mask
                subsam_mask = self._subsamples_masks.all(*self._subsamples_map[subs])
                # Calling hist manager with a subsample mask
                self.hists_managers[subs].fill_histograms(
                    self.events,
                    self.weights_manager,
                    self._cuts_masks,
                    subsample_mask=subsam_mask,
                    custom_fields=self.custom_histogram_fields,
                )
                # Saving the output for each subsample
                for var, H in self.hists_managers[subs].get_histograms().items():
                    self.output["variables"][var][subs] = H
        else:
            self.hists_managers[self._sample].fill_histograms(
                self.events,
                self.weights_manager,
                self._cuts_masks,
                custom_fields=self.custom_histogram_fields,
            )

            # Saving in the output the filled histograms for the current sample
            for var, H in self.hists_managers[self._sample].get_histograms().items():
                self.output["variables"][var][self._sample] = H

    def fill_histograms_extra(self):
        '''
        The function get called after the filling of the default histograms.
        Redefine it to fill custom histograms
        '''
        pass

    def define_column_accumulators(self):
        '''
        Define the ColumsManagers to handle the requested columns from the configuration.
        If Subsamples are defined a columnsmager is created for each of them.
        '''
        self.columns_managers = {}
        if self._hasSubsamples:
            for subs in self._subsamples_names:
                self.columns_managers[subs] = ColumnsManager(
                    self.cfg.columns, subs, self._categories
                )
        else:
            self.columns_managers[self._sample] = ColumnsManager(
                self.cfg.columns, self._sample, self._categories
            )

    def define_column_accumulators_extra(self):
        '''
        This function should be redefined to add column accumulators in the custom
        processor, if they cannot be defined from the configuration
        '''

    def fill_column_accumulators(self):
        if self._hasSubsamples:
            # call the filling for each
            for subs in self._subsamples_names:
                # Get the mask
                subsam_mask = self._subsamples_masks.all(*self._subsamples_map[subs])
                # Calling hist manager with a subsample mask
                self.output["columns"][subs] = self.columns_managers[subs].fill_columns(
                    self.events,
                    self._cuts_masks,
                    subsample_mask=subsam_mask,
                )
        else:
            self.output["columns"][self._sample] = self.columns_managers[
                self._sample
            ].fill_columns(
                self.events,
                self._cuts_masks,
            )

    def fill_column_accumulators_extra(self):
        pass

    def process_extra_before_skim(self):
        pass

    def process_extra_before_presel(self):
        pass

    def process_extra_after_presel(self):
        pass

    def process(self, events: ak.Array):
        '''
        This function get called by Coffea on each chunk of NanoAOD file.
        The processing steps of PocketCoffea are defined in this function.

        Customization points for user-defined processor are provided as
        `_extra` functions. By redefining those functions the user can change the behaviour
        of the processor in some predefined points.

        The processing goes through the following steps:

          - load metadata
          - Skim events (first masking of events):
              HLT triggers should be applied here, but their use is left to the configuration,
              and not hardcoded in the processor.
          - apply object preselections
          - count objects
          - apply event preselection (secondo masking of events)
          - define categories
          - define weights
          - define histograms
          - count events in each category
        '''

        self.events = events

        ###################
        # At the beginning of the processing the initial number of events
        # and the sum of the genweights is stored for later use
        #################
        self.load_metadata()
        self.load_metadata_extra()
        # Define the accumulator instance for this chunk
        self.output = copy.deepcopy(self.output_format)

        self.nEvents_initial = self.nevents
        self.output['cutflow']['initial'][self._sample] += self.nEvents_initial
        if self._isMC:
            # This is computed before any preselection
            self.output['sum_genweights'][self._sample] = ak.sum(self.events.genWeight)
            if self._hasSubsamples:
                for subs in self._subsamples_names:
                    self.output['sum_genweights'][subs] = self.output['sum_genweights'][
                        self._sample
                    ]

        self.weights_config = self.weights_config_allsamples[self._sample]
        ########################
        # Then the first skimming happens.
        # Events that are for sure useless are removed.
        # The user can specify in the configuration a function to apply for the skiming.
        # BE CAREFUL: objects are not corrected and cleaned at this stage, the skimming
        # selections MUST be loose and inclusive w.r.t the final selections.
        #########################
        # Customization point for derived workflows before skimming
        self.process_extra_before_skim()
        # MET filter, lumimask, + custom skimming function
        self.skim_events()
        if not self.has_events:
            return self.output

        #########################
        # After the skimming we apply the object corrections and preselection
        # Doing so we avoid to compute them on the full NanoAOD dataset
        #########################
        # Apply preselections
        self.apply_object_preselection()
        self.count_objects()
        # Compute variables after object preselection
        self.define_common_variables_before_presel()
        # Customization point for derived workflows after preselection cuts
        self.process_extra_before_presel()

        # This will remove all the events not passing preselection from further processing
        self.apply_preselections()

        # If not events remains after the preselection we skip the chunk
        if not self.has_events:
            return self.output

        ##########################
        # After the preselection cuts has been applied more processing is performwend
        ##########################
        # Customization point for derived workflows after preselection cuts
        self.define_common_variables_after_presel()
        self.process_extra_after_presel()

        # This function applies all the cut functions in the cfg file
        # Each category is an AND of some cuts.
        self.define_categories()

        # Weights
        self.compute_weights()
        self.compute_weights_extra()

        # Create the HistManager
        self.define_custom_axes_extra()
        self.define_histograms()
        self.define_histograms_extra()
        self.define_column_accumulators()
        self.define_column_accumulators_extra()

        # Fill histograms
        self.fill_histograms()
        self.fill_histograms_extra()
        self.fill_column_accumulators()
        self.fill_column_accumulators_extra()

        # Count events
        self.count_events()

        return self.output

    def postprocess(self, accumulator):
        '''
        The function is called by coffea at the end of the processing.
        The total sum of the genweights is computed for the MC samples
        and the histogram is rescaled by 1/sum_genweights.
        The `sumw` output is corrected accordingly.

        To add additional customatizaion redefine the `postprocessing` function,
        but remember to include a super().postprocess() call.
        '''
        # Rescale MC histograms by the total sum of the genweights
        scale_genweight = {}
        for sample in self._totalSamplesSet:
            scale_genweight[sample] = (
                1
                if sample.startswith('DATA')  # BEAWARE OF THIS HARDCODING
                else 1.0 / accumulator['sum_genweights'][sample]
            )
            # correct also the sumw (sum of weighted events) accumulator
            for cat in self._categories:
                accumulator["sumw"][cat][sample] *= scale_genweight[sample]

        for var, hists in accumulator["variables"].items():
            # Rescale only histogram without no_weights option
            if self.cfg.variables[var].no_weights:
                continue
            for sample, h in hists.items():
                h *= scale_genweight[sample]
        accumulator["scale_genweight"] = scale_genweight
        return accumulator

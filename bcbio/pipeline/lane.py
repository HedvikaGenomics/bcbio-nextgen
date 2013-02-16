"""Top level driver functionality for processing a sequencing lane.
"""
import os
import copy

from bcbio.log import logger
from bcbio import utils, broad
from bcbio.pipeline.fastq import get_fastq_files
from bcbio.pipeline.demultiplex import split_by_barcode
from bcbio.pipeline.alignment import align_to_sort_bam
from bcbio.ngsalign.split import split_read_files
from bcbio.bam.trim import brun_trim_fastq, cutadapt_trim


def _prep_fastq_files(item, bc_files, dirs, config):
    """Potentially prepare input FASTQ files for processing.
    """
    fastq1, fastq2 = bc_files[item["barcode_id"]]
    split_size = config.get("distributed", {}).get("align_split_size",
                                                   config["algorithm"].get("align_split_size", None))
    if split_size:
        split_dir = utils.safe_makedir(os.path.join(dirs["work"], "align_splitprep", item["description"]))
        return split_read_files(fastq1, fastq2, item, split_size, split_dir, dirs, config)
    else:
        return [[fastq1, fastq2, None]]

def process_lane(lane_items, fc_name, fc_date, dirs, config):
    """Prepare lanes, potentially splitting based on barcodes.
    """
    lane_name = "%s_%s_%s" % (lane_items[0]['lane'], fc_date, fc_name)
    logger.info("Demulitplexing %s" % lane_name)
    full_fastq1, full_fastq2 = get_fastq_files(dirs["fastq"], dirs["work"],
                                               lane_items[0], fc_name,
                                               dirs=dirs,
                                               config=_update_config_w_custom(config, lane_items[0]))
    bc_files = split_by_barcode(full_fastq1, full_fastq2, lane_items,
                                lane_name, dirs, config)
    out = []
    for item in lane_items:
        config = _update_config_w_custom(config, item)
        # Can specify all barcodes but might not have actual sequences
        # Would be nice to have a good way to check this is okay here.
        if bc_files.has_key(item["barcode_id"]):
            for fastq1, fastq2, lane_ext in _prep_fastq_files(item, bc_files, dirs, config):
                cur_lane_name = lane_name
                cur_lane_desc = item["description"]
                if item.get("name", "") and config["algorithm"].get("include_short_name", True):
                    cur_lane_desc = "%s : %s" % (item["name"], cur_lane_desc)
                if item["barcode_id"] is not None:
                    cur_lane_name += "_%s" % (item["barcode_id"])
                if lane_ext is not None:
                    cur_lane_name += "_s{0}".format(lane_ext)
                out.append((fastq1, fastq2, item, cur_lane_name, cur_lane_desc,
                            dirs, config))
                """
                if config["algorithm"].get("trim_reads", False):
                    trim_info = brun_trim_fastq([x for x in [fastq1, fastq2] if x is not None],
                                                dirs, config)
                    fastq1 = trim_info[0]
                    if fastq2 is not None:
                        fastq2 = trim_info[1]
                out.append((fastq1, fastq2, item, cur_lane_name, cur_lane_desc,
                            dirs, config))
                            """
    return out


def trim_lane(fastq1, fastq2, info, lane_name, lane_desc, dirs, config):
    """
    if trim_reads is set with no trimmer specified, default to B-run trimming
    only. if trimmer is set to a supported type, perform that trimming
    instead.

    """
    # this block is to maintain legacy configuration files
    if not config["algorithm"].get("trim_reads", False):
        return [(fastq1, fastq2, info, lane_name, lane_desc, dirs, config)]

    # swap the default to None if trim_reads gets deprecated
    trimmer = config["algorithm"].get("trimmer", "low_quality")

    to_trim = [x for x in [fastq1, fastq2] if x is not None]

    if trimmer == "low_quality":
        logger.info("Trimming low quality ends from %s."
                    % (", ".join(to_trim)))
        out_files = brun_trim_fastq(to_trim, dirs, config)

    elif trimmer == "adapter":
        logger.info("Trimming low quality ends and adapter sequence "
                    "from %s." % (", ".join(to_trim)))
        out_files = cutadapt_trim(to_trim, dirs, config)
    else:
        logger.info("Skipping trimming of %s." % (", ".join(to_trim)))
        out_files = [fastq1, fastq2]

    fastq1 = out_files[0]
    if fastq2 is not None:
        fastq2 = out_files[1]

    return [(fastq1, fastq2, info, lane_name, lane_desc, dirs, config)]


def process_alignment(fastq1, fastq2, info, lane_name, lane_desc,
                      dirs, config):
    """Do an alignment of fastq files, preparing a sorted BAM output file.
    """
    aligner = config["algorithm"].get("aligner", None)
    out_bam = ""
    if os.path.exists(fastq1) and aligner:
        logger.info("Aligning lane %s with %s aligner" % (lane_name, aligner))
        out_bam = align_to_sort_bam(fastq1, fastq2, info["genome_build"], aligner,
                                    lane_name, lane_desc, dirs, config)
    elif os.path.exists(fastq1) and fastq1.endswith(".bam"):
        sort_method = config["algorithm"].get("bam_sort")
        if sort_method:
            runner = broad.runner_from_config(config)
            out_file = os.path.join(dirs["work"], "{}-sort.bam".format(
                os.path.splitext(os.path.basename(fastq1))[0]))
            out_bam = runner.run_fn("picard_sort", fastq1, sort_method, out_file)
        else:
            out_bam = fastq1
    return [{"fastq": [fastq1, fastq2], "out_bam": out_bam, "info": info,
             "config": config}]

def _update_config_w_custom(config, lane_info):
    """Update the configuration for this lane if a custom analysis is specified.
    """
    name_remaps = {"variant": ["SNP calling", "variant"],
                   "SNP calling": ["SNP calling", "variant"]}
    config = copy.deepcopy(config)
    base_name = lane_info.get("analysis")
    for analysis_type in name_remaps.get(base_name, [base_name]):
        custom = config["custom_algorithms"].get(analysis_type, None)
        if custom:
            for key, val in custom.iteritems():
                config["algorithm"][key] = val
    # apply any algorithm details specified with the lane
    for key, val in lane_info.get("algorithm", {}).iteritems():
        config["algorithm"][key] = val
    return config

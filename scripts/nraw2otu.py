"""

OVERVIEW: 

Script to convert raw 16S sequencing data into OTU tables.  Acts on a directory containing a summary file and the raw data.
Outputs a directory with processing results.

"""

from __future__ import print_function
from optparse import OptionParser
import numpy as np
import os, sys
import os.path
import math
from string import ascii_lowercase
import multiprocessing as mp
import ntpath
import preprocessing_16S as OTU
import Formatting as frmt
from CommLink import *
from SummaryParser import *
from Features import *
import pickle
import QualityControl as QC

# Read in arguments for the script
usage = "%prog -i INPUT_DIR -o OUTPUT_DIR_FULLPATH"
parser = OptionParser(usage)
parser.add_option("-i", "--input_dir", type="string", dest="input_dir")
parser.add_option("-o", "--output_dir", type="string", dest="output_dir")
parser.add_option("-p", "--primers_removed", dest="primers_removed", default='False')
parser.add_option("-b", "--split_by_barcodes", dest="split_by_barcodes", default='False')
parser.add_option("-m", "--multiple_files", dest="multiple_raw_files", default='False')
(options, args) = parser.parse_args()

if( not options.input_dir ):
    parser.error("No data directory specified.")

# Parse summary file
summary_file = options.input_dir + '/summary_file.txt'
summary_obj = SummaryParser(summary_file)
summary_obj.ReadSummaryFile()
dataset_ID = summary_obj.datasetID

# Pipe stdout and stderr to logfiles in the new directory
sys.stdout = open('/home/ubuntu/logs/stdout_' + dataset_ID + '_proc_16S.log','w')
sys.stderr = open('/home/ubuntu/logs/stderr_' + dataset_ID + '_proc_16S.log','w')
def warning(*objs):
    print("WARNING: ", *objs, file=sys.stderr)

# Check for presence of metadata map - report if metadata is missing.
try:
    metadata_file = summary_obj.attribute_value_16S['METADATA_FILE']
except:
    metadata_file = None
    warning("No metadata file found!!  This will cause problems downstream...")

# If no output directory specified, default to $home/proc/
homedir = os.getenv("HOME")
if( not options.output_dir ):
    print("No output directory name specified.  Writing to " + homedir + "/proc/ by default.")
    options.output_dir = homedir + '/proc/' + dataset_ID + '_proc_16S'

# Make a directory for the 16S processing results 
working_directory = options.output_dir
try:
    os.system('mkdir ' + working_directory)
except:
    print("Processing directory for this dataset already exists.  Overwriting its contents.")

# If OTU clustering is to be performed, check for whether percent similarity was specified in summary file
try: 
    similarity = float(summary_obj.attribute_value_16S['OTU_SIMILARITY'])
except:
    similarity = 97.0


# Extract file locations
primers_file = options.input_dir + '/' + summary_obj.attribute_value_16S['PRIMERS_FILE']
barcodes_map = options.input_dir + '/' + summary_obj.attribute_value_16S['BARCODES_MAP']
try:
    raw_data_file = options.input_dir + '/' + summary_obj.attribute_value_16S['RAW_FASTQ_FILE']
    raw_file_type = 'FASTQ'
except:
    print("No single raw FASTQ file found.  Checking for raw FASTA.")
    try:
        raw_data_file = options.input_dir + '/' + summary_obj.attribute_value_16S['RAW_FASTA_FILE']
        raw_file_type = 'FASTA'
    except:
        print("No single raw FASTA file found either.  Checking for multiple files.")
        try:
            raw_data_summary_file = os.path.join(options.input_dir, summary_obj.attribute_value_16S['RAW_FASTQ_FILES'])
            raw_file_type = 'FASTQ'
        except:
            print("No filename of multiple raw FASTQs map provided.  Check contents of your raw data and summary file.")
            raise NameError("Unable to retrieve raw sequencing files.")

# Construct output filenames from dataset ID
fastq_trimmed_qual = working_directory + '/' + dataset_ID + '.raw_trimmed_qual.fastq'
fasta_trimmed = working_directory + '/' + dataset_ID + '.raw_trimmed.fasta'
fastq_trimmed_length = working_directory + '/' + dataset_ID + '.raw_length_trimmed.fastq'
fastq_trimmed_primers = working_directory + '/' + dataset_ID + '.raw_primers_trimmed.fastq'
fastq_split_by_barcodes = working_directory + '/' + dataset_ID + '.raw_split_by_barcodes.fastq'
fasta_dereplicated = working_directory + '/' + dataset_ID + '.raw_dereplicated.fasta'
dereplication_map = working_directory + '/' + dataset_ID + '.dereplication_map'

OTU_clustering_results = working_directory + '/' + dataset_ID + '.otu_clustering.' + str(int(similarity)) + '.tab'
OTU_table = working_directory + '/' + dataset_ID + '.otu_table.' + str(int(similarity))
OTU_sequences_fasta = working_directory + '/' + dataset_ID + '.otu_seqs.' + str(int(similarity)) + '.fasta'
OTU_sequences_table = working_directory + '/' + dataset_ID + '.otu_seqs.' + str(int(similarity)) + '.table'
oligotype_table_filename = working_directory + '/' + dataset_ID + '.oligotype_table.' + str(int(similarity)) + '.classic'

# Get ASCII encoding of FASTQ files
try:
    encoding = summary_obj.attribute_value_16S['ASCII_ENCODING']
except:
    encoding = ''

if(encoding == "ASCII_BASE_33"):
    print("ASCII 33 encoding for quality scores specified.")
    ascii_encoding = 33
elif(encoding == "ASCII_BASE_64"):
    print ("ASCII 64 encoding for quality scores specified.")
    ascii_encoding = 64
else:
    print ("No ASCII encoding specified in the summary file for the quality scores in the FASTQ file.  Using ASCII 64 as default.")
    warning("No ASCII encoding specified in the summary file for the quality scores in the FASTQ file.  Using ASCII 64 as default.")
    ascii_encoding = 64


# Parallel steps:
#       1. split fastq into chunks
#       2. demultiplex (sort by barcodes), remove primers, and trim, and convert to fasta format
#       3. recombine into a single fasta file before dereplicating

os.chdir(working_directory)


# Checkpoint - single or multiple raw files?  If multiple, the assumption is they are demultiplexed, where each raw file corresponds to a single sample's reads.
if options.multiple_raw_files == 'False':

    # Step 1.1 - get raw data filesize, then split into ~10Mb pieces (100000 lines) if smaller than 100 Mb, or into ~100Mb pieces (1000000 lines) otherwise.  Can optimize this eventually
    # to split according to the number of cpus.
    rawfilesize = os.path.getsize(raw_data_file)

    # Step 1.1 - split file into 1000000 line (~100Mb) chunks
    if(rawfilesize < 2e8):
        os.system('split -l 100000 ' + raw_data_file)
    else:
        os.system('split -l 1000000 ' + raw_data_file)

    # Step 1.2 - get split filenames
    split_filenames = []
    for c1 in ascii_lowercase:
        for c2 in ascii_lowercase:
            filename = 'x'+c1+c2
            if(os.path.isfile(filename)):
                split_filenames.append(filename)
    if len(split_filenames) == 0:
        split_filenames = [raw_data_file]
    raw_filenames = split_filenames

else:
    
    # If multiple raw sequence files are provided in a separate summary file, check integrity of the summary file and then extract raw file names.
    print('Reading fastqs from ' + raw_data_summary_file)
    with open(raw_data_summary_file, 'r') as fid:
        all_lines = fid.readlines()
    
    # Check for contents
    if len(all_lines) == 0:
        raise NameError("Raw data summary file named '" + raw_data_summary_file + "' appears to be empty.  Check its contents.")
    # Check for tab delimitation and empty space characters
    for line in all_lines:
        if len(line.split(' ')) > 1:
            raise NameError("Empty space characters detected in raw data summary file '" + raw_data_summary_file + "'.  Please make tab-delimited.")
        if len(line.split('\t')) == 1:
            if len(line.rstrip('\n')) > 0:
                raise NameError("No tab characters found in raw data summary file '" + raw_data_summary_file + "'.  Please make tab-delimited.")
            else:
                raise NameError("Empty lines found in raw data summary file '" + raw_data_summary_file + "'.  Please remove these before proceeding.")

    raw_filenames_orig = [os.path.join(options.input_dir, line.split('\t')[0]) for line in all_lines if len(line.rstrip('\n')) > 0]
    sampleID_map = [line.split('\t')[1].rstrip('\n') for line in all_lines if len(line.strip('\n')) > 0]
    raw_filenames = [line.split('\t')[0] for line in all_lines if len(line.rstrip('\n')) > 0]
    for i in range(len(raw_filenames_orig)):
        cmd_str = 'cp ' + raw_filenames_orig[i] + ' ' + raw_filenames[i]
        os.system(cmd_str)
    split_filenames = raw_filenames


# Do quality control steps (generate read length histograms etc.)
QCpath = os.path.join(working_directory, 'quality_control')
try:
    os.system('mkdir ' + QCpath)
except:
    print("Unable to create quality control directory.  Already exists?")
QC.read_length_histogram(split_filenames[0], QCpath, raw_file_type)
    

# Check whether samples need to be split by barcodes and primers need to be removed
if (options.split_by_barcodes == 'True' and options.primers_removed == 'True' and options.multiple_raw_files == 'False'):
    # Copy the raw file into processed folder and call it trimmed by primers
    for split_filename in split_filenames:
        cmd_str = 'cp ' + split_filename + ' ' + split_filename + '.sb.pt'
        os.system(cmd_str)
    
# Step 2 - loop through these split files and launch parallel threads as a function of the number of CPUs
cpu_count = mp.cpu_count()

# Step 2.1 - demultiplex, i.e. sort by barcode
if (options.split_by_barcodes == 'False' and options.multiple_raw_files == 'False'):
    mode = summary_obj.attribute_value_16S['BARCODES_MODE']
    pool = mp.Pool(cpu_count)
    filenames = split_filenames
    newfilenames = [f + '.sb' for f in filenames]
    barcodes_map_vect = [barcodes_map]*len(filenames)
    mode_vect = [mode]*len(filenames)
    pool.map(OTU.split_by_barcodes, zip(filenames, newfilenames, barcodes_map_vect, mode_vect))
    pool.close()
    pool.join()
    split_filenames = [f + '.sb' for f in split_filenames] 
elif (options.multiple_raw_files == 'True'):
    # If multiple raw files each corresponding to a sample are provided, rename sequence IDs according to the raw file summary sample IDs provided
    pool = mp.Pool(cpu_count)
    filenames = split_filenames
    newfilenames = [f + '.sb' for f in filenames]
    pool.map(OTU.replace_seqIDs_for_demultiplexed_files, zip(filenames, newfilenames, sampleID_map))
    pool.close()
    pool.join()
    split_filenames = [f + '.sb' for f in split_filenames]

# Step 2.2 - remove primers
if (options.primers_removed == 'False'):
    pool = mp.Pool(cpu_count)
    filenames = split_filenames
    newfilenames = [f + '.pt' for f in filenames]
    primers_vect = [primers_file]*len(filenames)
    pool.map(OTU.remove_primers, zip(filenames, newfilenames, primers_vect))
    pool.close()
    pool.join()
    split_filenames = [f + '.pt' for f in split_filenames] 


# Step 2.3 - trim with quality filter
if (raw_file_type == "FASTQ"):
    pool = mp.Pool(cpu_count)
    filenames = split_filenames
    newfilenames = [f + '.qt' for f in filenames]
    ascii_vect = [ascii_encoding]*len(filenames)

    pool.map(OTU.trim_quality, zip(filenames, newfilenames, ascii_vect))
    pool.close()
    pool.join()
    split_filenames = [f + '.qt' for f in split_filenames] 


# Step 2.4 - trim to uniform length of 101
try: 
    length = summary_obj.attribute_value_16S['TRIM_LENGTH']
except:
    length = 101
pool = mp.Pool(cpu_count)
filenames = split_filenames
newfilenames = [f + '.lt' for f in filenames]
length_vect = [length]*len(filenames)
ascii_vect = [ascii_encoding]*len(filenames)
    
if (raw_file_type == "FASTQ"):
    pool.map(OTU.trim_length_fastq, zip(filenames, newfilenames, length_vect, ascii_vect))
    pool.close()
    pool.join()
    split_filenames = [f + '.lt' for f in split_filenames] 
else:
    pool.map(OTU.trim_length_fasta, zip(filenames, newfilenames, length_vect))
    pool.close()
    pool.join()
    split_filenames = [f + '.lt' for f in split_filenames] 

# Step 2.5 - convert to FASTA format
if (raw_file_type == "FASTQ"):
    pool = mp.Pool(cpu_count)
    filenames = split_filenames
    newfilenames = [f + '.fasta' for f in filenames]
    pool.map(frmt.fastq2fasta, zip(filenames, newfilenames))
    pool.close()
    pool.join()
    split_filenames = [f + '.fasta' for f in split_filenames] 

# Step 2.6 - renumber sequences IDs to be consistent across files
try:
    separator = summary_obj.attribute_value_16S['BARCODES_SEPARATOR']
except:
    separator = '_'
OTU.renumber_sequences(split_filenames, separator)


# Step 3 - Recombine into a single fasta file
if len(split_filenames)>1:
    cat_str = ['cat']
    for filename in split_filenames:
        cat_str.append(filename)
    cat_str = ' '.join(cat_str)
    cat_str = cat_str + ' > ' + fasta_trimmed    
    # Recombine
    os.system(cat_str)
else:
    os.system('cp ' + split_filenames[0] + ' ' + fasta_trimmed)


# Dereplicate sequences into a list of uniques for clustering
OTU.dereplicate_and_sort(fasta_trimmed, fasta_dereplicated, dereplication_map, '_')

# Remove chimeras and cluster OTUs
OTU.remove_chimeras_and_cluster_OTUs(fasta_dereplicated, OTU_sequences_fasta, OTU_sequences_table, OTU_clustering_results, relabel=True, cluster_percentage=similarity)


############################
#
# OTU and oligotype calling
#
############################

# Build de novo oligotype table - annotate sequences as 'OTU_ID.oligotype_ID' and compute counts for each oligotype
OTU.compute_oligotype_table(fasta_trimmed, fasta_dereplicated, OTU_clustering_results, '_', oligotype_table_filename)


# Obtain Greengenes reference IDs for dereplicated sequences
alignment_results = working_directory + '/gg_alignments.aln'
uc_results = working_directory + '/gg_alignments.uc'
similarity_float = float(similarity)/100
GG_database_to_use = '/home/ubuntu/gg_13_5_otus/rep_set/' + str(int(similarity)) + '_otus.fasta'
if not os.path.isfile(GG_database_to_use):
    raise NameError('Percent similarity ID (' + str(similarity) + '%) does not have a matching GG database.  This will break downstream steps.')
cmd_str = '/home/ubuntu/bin/usearch8 -usearch_local ' + fasta_dereplicated + ' -db ' + GG_database_to_use + ' -strand both -id ' + str(similarity_float) + ' -alnout ' + alignment_results + ' -uc ' + uc_results
os.system(cmd_str)

# Extract alignment dictionary
OTU_GG_dict = OTU.parse_alignment(alignment_results)

# Separate out GG-referenced reads from the rest which will be clustered as de novo OTUs
GG_reads_fasta = os.path.join(working_directory, 'gg_reads.fasta')
denovo_reads_fasta = os.path.join(working_directory, 'denovo_reads.fasta')
OTU.separate_GG_reads(fasta_dereplicated, OTU_GG_dict, GG_reads_fasta, denovo_reads_fasta)

# Cluster remaining reads
denovo_OTU_sequences = os.path.join(working_directory, dataset_ID + '.denovo_otus.fasta')
denovo_OTU_seqs_table = os.path.join(working_directory, dataset_ID + '.denovo_otus.table')
denovo_clustering_results = os.path.join(working_directory, dataset_ID + '.denovo_clustering.tab')
if os.path.isfile(denovo_reads_fasta):
    OTU.remove_chimeras_and_cluster_OTUs(denovo_reads_fasta, denovo_OTU_sequences, denovo_OTU_seqs_table, denovo_clustering_results, relabel=True, cluster_percentage=similarity)

    # Recompute a de novo oligotype table for de novo reads
    denovo_oligotype_table = os.path.join(working_directory, dataset_ID + '.denovo_oligotype_table.classic')
    denovo_only_otu_table = os.path.join(working_directory, dataset_ID + '.denovo_only_otu_table.classic')
    OTU.compute_oligotype_table(fasta_trimmed, denovo_reads_fasta, denovo_clustering_results, '_', denovo_oligotype_table)
    OTU.collapse_oligotypes(denovo_oligotype_table, denovo_only_otu_table)

# Build 3 OTU tables - one completely de novo (equivalent to collapsed oligotype table)
#                    - one only with OTUs that matched a Greengenes sequence (for use in PiCRUST)
#                    - one open reference 

# De novo OTU table in classic dense format
OTU_table_classic = OTU_table + '.classic'
OTU.collapse_oligotypes(oligotype_table_filename, OTU_table_classic)

# Closed reference OTU table (GreenGenes-referenced)
OTU_table_gg = OTU_table + '.gg'
OTU.build_GG_OTU_table(dereplication_map, OTU_GG_dict, OTU_table_gg)

# Convert GG OTU to classic format
OTU_table_gg_classic = OTU_table_gg + '.classic'
frmt.convert_OTU_to_classic_dense_format(OTU_table_gg, OTU_table_gg_classic)

# Concatenate GG table and denovo table
if os.path.isfile(denovo_reads_fasta):
    open_reference_OTU_table = OTU_table + '.open_ref.classic'
    OTU.concatenate_OTU_tables(OTU_table_gg_classic, denovo_only_otu_table, open_reference_OTU_table)


# Convert OTU counts to relative abundances.  These will become the default OTU tables.
OTU_table_classic_relative_abundances = OTU_table + '.classic.relative'
OTU_table_gg_classic_relative_abundances = OTU_table_gg + '.classic.relative'
frmt.convert_to_relative_abundances(OTU_table_classic, OTU_table_classic_relative_abundances)
frmt.convert_to_relative_abundances(OTU_table_gg_classic, OTU_table_gg_classic_relative_abundances)


##################
#
#   Basic quality control
#
###################

# Print barplot of readcounts per sample
QC.sample_read_counts(OTU_table_classic, QCpath)

# Write out number of reads thrown out at each step
QC.reads_thrown_out_at_each_step(raw_filenames, os.path.join(QCpath, 'processing_summary.txt'))


#################################################
#
# Put all results and metadata file in a single folder
#
#################################################

dataset_folder = dataset_ID + '_results'
try:
    os.system('mkdir ' + dataset_folder)
except:
    print('Results directory already exists.  Overwriting its contents.')

os.system('cp -r ' + QCpath + ' ' + dataset_folder + '/.')

# Open ref, closed ref, fully de novo and oligotype tables
try:
    os.system('cp ' + open_reference_OTU_table + ' ' + dataset_folder + '/.')
    os.system('cp ' + denovo_only_otu_table + ' ' + dataset_folder + '/.')
except:
    skip
os.system('cp ' + OTU_table_gg_classic + ' ' + dataset_folder + '/.')
os.system('cp ' + OTU_table_classic + ' ' + dataset_folder + '/.')
os.system('cp ' + oligotype_table_filename + ' ' + dataset_folder + '/.')
os.system('cp ' + OTU_table_gg_classic_relative_abundances + ' ' + dataset_folder + '/.')
os.system('cp ' + OTU_table_classic_relative_abundances + ' ' + dataset_folder + '/.')

# OTU sequences
os.system('cp ' + OTU_sequences_table + ' ' + dataset_folder + '/.')
os.system('cp ' + OTU_sequences_fasta + ' ' + dataset_folder + '/.')

if metadata_file is not None:
    os.system('cp ' + options.input_dir + '/' + metadata_file + ' ' + dataset_folder + '/.')

# Put the summary file in the folder and change the summary file path to its new location
os.system('cp ' + summary_file + ' ' + dataset_folder + '/.')
summary_obj.summary_file = dataset_folder + '/summary_file.txt'
summary_obj.attribute_value_16S['OTU_TABLE_CLOSED_REF'] = ntpath.basename(OTU_table_gg)
summary_obj.attribute_value_16S['OTU_TABLE_DENOVO'] = ntpath.basename(OTU_table_classic)
summary_obj.attribute_value_16S['OLIGOTYPE_TABLE'] = ntpath.basename(oligotype_table_filename)
summary_obj.attribute_value_16S['OTU_SEQUENCES_TABLE'] = ntpath.basename(OTU_sequences_table)
summary_obj.attribute_value_16S['OTU_SEQUENCES_FASTA'] = ntpath.basename(OTU_sequences_fasta)
try:
    summary_obj.attribute_value_16S['METADATA_FILE'] = ntpath.basename(metadata_file)
except:
    summary_obj.attribute_value_16S['METADATA_FILE'] = "None"
summary_obj.attribute_value_16S['PROCESSED'] = 'True'
summary_obj.WriteSummaryFile()


# Transfer results 
processing_results_dir = '/home/ubuntu/processing_results'
os.system('cp -r ' + os.path.join(working_directory, dataset_folder) + ' ' + processing_results_dir + '/.')

'''
# Transfer to PiCRUST server and wait for results
cl = CommLink('proc')
results_folder = dataset_ID + '_results'
test = cl.launch_proc_listener(dataset_folder, results_folder)

# Move results from inbox to results folder
processing_results_dir = '/home/ubuntu/processing_results'
os.system('mv /home/ubuntu/inbox/' + results_folder + ' ' + processing_results_dir + '/.')
os.chdir(os.path.join(processing_results_dir, results_folder))


# Extract features
features = Features('summary_file.txt')
features.LoadOTUtable()
features.LoadPredictedMetagenome()
metapredL1 = os.path.join('/home/ubuntu/processing_results', results_folder, 'picrust_results/CRC_Zhao_2012.L1.biom')
metapredL2 = os.path.join('/home/ubuntu/processing_results', results_folder, 'picrust_results/CRC_Zhao_2012.L2.biom')
metapredL3 = os.path.join('/home/ubuntu/processing_results', results_folder, 'picrust_results/CRC_Zhao_2012.L3.biom')
features.LoadPredictedMetagenome(metapredL1)
features.LoadPredictedMetagenome(metapredL2)
features.LoadPredictedMetagenome(metapredL3)
features.LoadPhylogeneticFeatures()

# Pickle features
with open('pickled_features.pkl', 'wb') as fid:
    pickle.dump(features, fid)


###################
#
#  Final check - implement a better check in the future
#
###################


# Check for file size greater than zero - add more thorough check eventually
otu_proc_success = False
if(os.stat(OTU_table).st_size > 0 and os.stat(OTU_sequences_fasta).st_size > 0 and os.stat(OTU_sequences_table).st_size > 0):
    otu_proc_success = True

# Processing complete - if successful, update summary file and write.  Otherwise, leave untouched and exit.
if(otu_proc_success == True):
    print("Successfully processed 16S data!  Summary file has been updated.")
else:
    print("Failed to process 16S data.")

'''


# amplicon_sequencing_pipeline

Walking through `raw2otu.py`, which runs the code, should give you an overview of the steps in the pipeline 

## A few notes:
- it runs using Python 2.
- if you have anaconda2 installed, it should run without many dependency issues (there may be one or two but I don't think there are more than that).  
- to run (on a linux machine), you'll need to copy the scripts folder in the link above to your home directory (~/scripts), and run it using the command in the documentation. 
- I also forgot to mention that you need to install usearch8 as well, which is straightforward.

### Merging PE reads 
is a manual step which is not included in the pipeline, since people seem to have so many individual preferences there that make it difficult to automate.  For this step, I'd suggest using either ea-utils or pandaseq, both work fine for me.

### DADA2
people in the lab have been using it independently of the pipeline.  It is easy to install on the same machine and you can probably use the pipeline to get until a list of trimmed and processed reads, and a list of dereplicated reads, which I believe are inputs into DADA2 (I am not 100% sure of this because I haven't used it myself yet).  So if you wanted to use DADA2, you'd have to install that independently and reroute the pipeline through that somehow.  Shouldn't be difficult.


### does it re-map all reads to the OTUs ? 
This version doesn't include the mapping step either unfortunately, mostly because those of us using it are fine with the counts obtained from looking at the usearch clustering file results (where it says a unique read is either an OTU or assigned to an OTU cluster).  All other reads are thrown out as low count reads prior to dereplication.  
You may want to implement on your end if it was important for the FDA in terms of adhering to the uparse-approved pipeline, but I expect the results would be very similar.  




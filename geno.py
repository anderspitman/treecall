#!/usr/bin/env python2

# Author: Ni Huang <nihuang at genetics dot wustl dot edu>
# Author: Rachel Schwartz <Rachel dot Schwartz at asu dot edu>
# Author: Kael Dai <Kael dot Dai at asu dot edu>

from __future__ import print_function
import warnings
import signal
signal.signal(signal.SIGPIPE, signal.SIG_DFL)

import sys
import numpy as np
import vcf

with warnings.catch_warnings(ImportWarning):
    from ete2 import Tree

warnings.filterwarnings('error')

from utils import *

def read_vcf_records(filename, maxn=1000):
    """Read vcf file - get info about variants - need to clarify how this is different from read_vcf

    Args:
        filename: name of vcf file to read
        maxn (int): number of lines / sites in file to process

    Returns:
        np.array (tuple): variant info (chrom, pos, ref)
        np.array (int): Number of high-quality bases observed for each of the alleles
        np.array (double): List of Phred-scaled genotype likelihoods for all 10 possible genotypes

    """    
    print('read next %d sites' % maxn, end = ' ', file=sys.stderr)
    
    vcffile = vcf.Reader(open(filename, 'r'))
    variants,ADs,PLs = [],[],[]
    bases = ['A','C','G','T']
    i = 0
    for v in vcffile:
        i += 1
        print('.' , end = '', file=sys.stderr)
        if v.REF in bases and v.ALT[0] in bases:
            variants.append((v.CHROM,v.POS,v.REF))
            #ad for each sample for each allele
            ad = np.array([v.genotype(s).data.AD for s in vcffile.samples], dtype=np.uint16)                
            ADs.append(ad)
            
            s = [str(b) for b in v.ALT if str(b) in bases] #filter X
            s.insert(0,str(v.REF))
            #this is a silly way to find the correct genotypes in the pls
            if len(s) == 2:
                find_geno = {0:s[0]+s[0], 1:''.join(sorted(s[0]+s[1])), 2:s[1]+s[1]}
            elif len(s) == 3:
                find_geno = {0:s[0]+s[0], 1:''.join(sorted(s[0]+s[1])), 2:s[1]+s[1], 3:''.join(sorted(s[0]+s[2])), 4:''.join(sorted(s[1]+s[2])), 5:s[2]+s[2]}
            elif len(s) == 4:
                find_geno = {0:s[0]+s[0], 1:''.join(sorted(s[0]+s[1])), 2:s[1]+s[1], 3:''.join(sorted(s[0]+s[2])), 4:''.join(sorted(s[1]+s[2])), 5:s[2]+s[2], 6:''.join(sorted(s[0]+s[3])), 7:''.join(sorted(s[1]+s[3])), 8:''.join(sorted(s[2]+s[3])), 9:s[3]+s[3]}
                             
            #get pl for ref and alts
            pl = [v.genotype(s).data.PL for s in vcffile.samples]  #list of lists
            for j,p in enumerate(pl):
                pl_dict = {'AA':255,'AC':255,'AG':255,'AT':255,'CC':255,'CG':255,'CT':255,'GG':255,'GT':255,'TT':255} #all genos are unlikely
                
                #triallelic the PL pattern is RR,RA1,A1A1,RA2,A1A2,A2A2
                for o in range(len(find_geno)):
                    g = find_geno[o]
                    pl_dict[g] = p[o]  #pl for that geno
                assert len(pl_dict) == 10, sorted(pl_dict.keys())
                    
                #get PL for all 10 in alpha order as np
                pl[j] = np.array([float(geno_pl) for geno,geno_pl in sorted(pl_dict.items())], dtype = np.longdouble)  #float, but should be np array longdouble

            pl = np.array(pl)
            assert pl.shape == (len(vcffile.samples),10), pl.shape
            PLs.append(pl)
                
        if i == maxn:
            print('... %s:%s ...' % (v.CHROM, v.POS), end=' ', file=sys.stderr)
            break

    variants = np.array(variants)
    PLs = np.array(PLs)
    num_var, num_samp, num_geno = PLs.shape
    assert num_samp == len(vcffile.samples)
    assert num_geno == 10
 #   DPRs = np.array(ADs)

    print(' done', file=sys.stderr)
    return variants, ADs, PLs

def genotype_main(args):
    """
    uses init_tree, make_base_prior, make_mut_matrix, read_vcf_records, genotype
    
    Args:
        vcf: input vcf/vcf.gz file, "-" for stdin
        output: output basename
        tree: file containing lineage tree'
        nsite: number of sites processed once, default 1000
        mu: mutation rate in Phred scale, default 80
        het: heterozygous rate in Phred scale, default 30, 0 for uninformative
    """
    
    GTYPE10 = np.array(('AA','AC','AG','AT','CC','CG','CT','GG','GT','TT'))
    print(args, file=sys.stderr)

    tree = Tree(args.tree)
    tree = init_tree(tree)

    base_prior = make_base_prior(args.het, GTYPE10) # base genotype prior
    mm,mm0,mm1 = make_mut_matrix_gtype10(args.mu)#, GTYPE10) # substitution rate matrix, with non-diagonal set to 0, with diagonal set to 0

    fout = open(args.output, 'w')
    fout.close()
    fout = open(args.output, 'a')
    
    score = 0
    while True:
        variants, DPRs, PLs = read_vcf_records(args.vcf, args.nsite)
        records,s = genotype(PLs, tree, variants, mm, mm0, mm1, base_prior)
        np.savetxt(fout, records, fmt=['%s','%d','%s','%.2e','%.2e','%s','%.2e','%s','%s','%.2e','%d','%s'], delimiter='\t')
        score += s
        if len(PLs) < args.nsite:
            break
    print('sum(PL) = %.2f' % score)
    fout.close()

def genotype(PLs, tree, variants, mm, mm0, mm1, base_prior):
    """
    uses populate_tree_PL, calc_mut_likelihoods, phred2p
    """
    GTYPE10 = np.array(('AA','AC','AG','AT','CC','CG','CT','GG','GT','TT'))
    # calculate total likelihoods for each genotypes
    tree = populate_tree_PL(tree.copy(), PLs, mm, 'PL') # dim(tree.PL) = site x gtype
    tree_PL = tree.PL + base_prior
    # calculate no-mutation likelihoods for each genotypes
    #try:
    tree = populate_tree_PL(tree.copy(), PLs, mm0, 'PL0') # dim(tree.PL0) = site x gtype
    #except Exception as e:
    #    print('populate_tree_PL():', e, file=sys.stderr)
    #    sys.exit(1)
    tree_PL0 = tree.PL0 + base_prior
    
    # calculate mutation likelihoods for each genotypes and mutation locations
    tree = calc_mut_likelihoods(tree.copy(), mm0, mm1)
    mut_PLs = np.swapaxes(tree.PLm,0,1) # site x location x gtype
    mut_PLs += base_prior
    n,l,g = mut_PLs.shape # n sites, l locations, g gtypes
    nn = np.arange(n)

    k = tree_PL.argmin(axis=1) # most likely base genotype for each site
    tree_P_per_site = phred2p(tree_PL).sum(axis=1) # total tree likelihood

    k0 = tree_PL0.argmin(axis=1) # most likely non-mutation base genotype for each site
    null_PL = tree_PL0[nn,k0] # best non-mutation likelihood (across genotypes) for each site
    null_P_per_site = phred2p(tree_PL0).sum(axis=1) # total non-mutation likelihood

    k1 = np.array([np.unravel_index(s.argmin(), (l,g)) for s in mut_PLs]) # site x 2, most likely mutation event for each site
    k1l = k1[:,0] # most likely location
    k1g = k1[:,1] # most likely base genotype
    mut_PL = mut_PLs[nn,k1l,k1g] # best mutation likelihood (across location and genotypes) for each site
    mut_P_per_site = phred2p(mut_PLs).sum(axis=(1,2)) # total mutation likelihood

    null_PLs = np.array([node.PL0 for node in tree.iter_descendants(strategy='postorder')])
    k2 = null_PLs[k1l,nn,].argmin(axis=-1) # get most likely mutation mutant genotype

    node_sids = np.array([','.join(map(str,node.sid)) for node in tree.iter_descendants(strategy='postorder')])
    records = np.array(zip(
            variants[nn,0],                         # chrom
            variants[nn,1],                         # pos
            variants[nn,2],                         # ref
            null_P_per_site/tree_P_per_site,        # null_P
            mut_P_per_site/tree_P_per_site,         # mut_P
           #GTYPE10[k],                             # MLE_base_gtype
           #phred2p(tree_PL[nn,k])/tree_P_per_site, # MLE_base_gtype_P
            GTYPE10[k0],                            # MLE_null_base_gtype
            phred2p(null_PL)/tree_P_per_site,       # MLE_null_base_gtype_P
            GTYPE10[k1g],                           # MLE_mut_base_gtype
            GTYPE10[k2],                            # MLE_mut_alt_gtype
            phred2p(mut_PL)/tree_P_per_site,        # MLE_mut_base_gtype_P
            k1l,                                    # MLE_mut_location
            node_sids[k1l]),                        # MLE_mut_samples
        dtype=[
            ('chrom','a10'),('pos','i4'),('ref','a1'),
            ('null_p','f8'),('mut_p','f8'),
            ('null_base','a2'),('null_base_p','f8'),
            ('mut_base','a2'),('mut_alt','a2'),('mut_conf_p','f8'),
            ('mut_loc','i4'),('mut_smpl','a128')])
    score = p2phred(records['mut_p']+records['null_p']).sum()
    return records,score
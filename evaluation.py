import json
import pickle
from abc import ABC, abstractmethod
from math import exp
from os.path import join

import networkx as nx
from rdkit import Chem
from rdkit.Chem import Descriptors, AllChem
from rdkit.Chem.QED import qed
from rdkit.Chem.rdmolfiles import MolToSmiles, MolFromSmiles
from rdkit.Chem.rdMolDescriptors import CalcNumRotatableBonds

import pandas as pd

from rdkit.Chem import RDConfig
import os
import sys

sys.path.append(os.path.join(RDConfig.RDContribDir, 'SA_Score'))
import sascorer
import numpy as np

import requests
from urllib import parse

class EvaluationError(RuntimeError):

    def __init__(self, desc):
        self.desc = desc

    def __str__(self):
        return self.desc + " (Evaluation error)"


class EvaluationStrategyComposant(ABC):
    """
    Base class of all evaluation strategy composants.
    The subclasses are either EvaluationStrategy leafs that implement the computation of a property to evaluates
    solutions, or EvaluationStaregyComposite nodes that define a multi-objective strategy and contain themselves a set
    of EvaluationStrategy leaves.
    """

    def __init__(self):
        self.n_calls = 0

    @abstractmethod
    def keys(self):
        """
        Returning a unique list of key(s) describing the evaluator(s)
        :return list of string key(s)
        """
        pass

    @abstractmethod
    def get_population_scores(self):
        """
        Returning the scores of the complete population.
        :return total scores of the population (list), list of list of intermediate scores for each contained evaluator
        """
        pass

    @abstractmethod
    def evaluate_individual(self, individual, to_replace_idx=None):
        """
        Evaluation of a given individual.
        :param individual: individual to be evaluated.
        :param to_replace_idx: idx of individual to be replaced in the population
        :return: total score of given individual, list of intermediate scores for each contained evaluator
        """
        self.n_calls += 1

    @abstractmethod
    def compute_record_scores_init_pop(self, population):
        """
        Computing and recording the internal scores for the complete population at initialization
        :return: None
        """
        pass

    @abstractmethod
    def record_ind_score(self, idx, new_total_score, new_scores, new_individual):
        """
        Updating the scores of the individual at the given index
        :param idx: index
        :param new_total_score: total score of the individual
        :param new_scores: intermediate scores
        :param new_individual: new individual to be inserted at the given index
        :return:
        """

    @abstractmethod
    def end_step_population(self, pop):
        """
        Informing the evaluator that the step has reached its end, and giving the resulting population.
        """
        pass

    @abstractmethod
    def get_additional_population_scores(self):
        """
        The evaluator can assign additional scores on total population. This method returns a dictionary of key/values
        for these scores.
        """

        return {
            "objective_calls": self.n_calls
        }


class EvaluationStrategy(EvaluationStrategyComposant, ABC):
    """
    Leaf strategy to evaluate the individuals of a population by implementing the computation of a property.
    """

    def __init__(self):
        super().__init__()
        self.to_be_replaced_current_step_idx = None
        self.scores = None

    def compute_record_scores_init_pop(self, population):
        self.scores = []
        for idx, ind in enumerate(population):
            if ind is not None:
                self.scores.append(self.evaluate_individual(ind)[0])

    def record_ind_score(self, idx, new_total_score, new_scores, new_individual):
        if idx == len(self.scores):
            self.scores.append(None)
        self.scores[idx] = new_total_score

    def get_population_scores(self):
        return self.scores, np.array([self.scores])

    def end_step_population(self, pop):
        pass

    def evaluate_individual(self, individual, to_replace_idx=None):
        super().evaluate_individual(individual, to_replace_idx)

    def get_additional_population_scores(self):
        return super().get_additional_population_scores()


class GenericFunctionEvaluationStrategy(EvaluationStrategy):
    """
    Evaluating individuals with a given function evaluating a SMILES representation of a molecule
    """

    def __init__(self, evaluation_function, function_name="custom_function"):
        """
        :param evaluation_function: must evaluate a SMILES representation with a value
        :param function_name: name of the function
        """
        super().__init__()
        self.evaluation_function = evaluation_function
        self.function_name = function_name

    def keys(self):
        return [self.function_name]

    def evaluate_individual(self, individual, to_replace_idx=None):
        super().evaluate_individual(individual, to_replace_idx)
        score = self.evaluation_function(individual.to_aromatic_smiles())
        return score, [score]


class ZincNormalizedPLogPEvaluationStrategy(EvaluationStrategy):

    def __init__(self):
        super().__init__()
        self.scores = None

    def keys(self):
        return ["penalized_logP"]

    def evaluate_individual(self, individual, to_replace_idx=None):
        """
        from https://github.com/bowenliu16/rl_graph_generation/blob/master/gym-molecule/gym_molecule/envs/molecule.py
        """
        super().evaluate_individual(individual, to_replace_idx)

        # normalization constants, statistics from 250k_rndm_zinc_drugs_clean.smi
        logP_mean = 2.4570953396190123
        logP_std = 1.434324401111988
        SA_mean = -3.0525811293166134
        SA_std = 0.8335207024513095
        cycle_mean = -0.0485696876403053
        cycle_std = 0.2860212110245455

        mol_graph = MolFromSmiles(individual.to_aromatic_smiles())

        log_p = Descriptors.MolLogP(mol_graph)
        SA = -sascorer.calculateScore(mol_graph)

        # cycle score
        cycle_list = nx.cycle_basis(nx.Graph(
            Chem.rdmolops.GetAdjacencyMatrix(mol_graph)))
        if len(cycle_list) == 0:
            cycle_length = 0
        else:
            cycle_length = max([len(j) for j in cycle_list])
        if cycle_length <= 6:
            cycle_length = 0
        else:
            cycle_length = cycle_length - 6
        cycle_score = -cycle_length

        normalized_log_p = (log_p - logP_mean) / logP_std
        normalized_SA = (SA - SA_mean) / SA_std
        normalized_cycle = (cycle_score - cycle_mean) / cycle_std

        score = normalized_log_p + normalized_SA + normalized_cycle

        return score, [score]


class PenalizedLogPEvaluationStrategy(EvaluationStrategy):
    """
    Evaluation of penalized logP
        from : https://github.com/google-research/google-research/blob/master/mol_dqn/chemgraph/dqn/py/molecules.py
    """

    def __init__(self):
        super().__init__()
        self.scores = None

    def keys(self):
        return ["penalized_logP"]

    def get_largest_ring_size(self, molecule):
        """
        Calculates the largest ring size in the molecule.
        Refactored from
        https://github.com/wengong-jin/icml18-jtnn/blob/master/bo/run_bo.py
        Args:
          molecule: Chem.Mol. A molecule.
        Returns:
          Integer. The largest ring size.
        """
        cycle_list = molecule.GetRingInfo().AtomRings()
        if cycle_list:
            cycle_length = max([len(j) for j in cycle_list])
        else:
            cycle_length = 0
        return cycle_length

    def evaluate_individual(self, individual, to_replace_idx=None):

        super().evaluate_individual(individual, to_replace_idx)

        mol_graph = MolFromSmiles(individual.to_aromatic_smiles())

        log_p = Descriptors.MolLogP(mol_graph)
        sas_score = sascorer.calculateScore(mol_graph)
        largest_ring_size = self.get_largest_ring_size(mol_graph)
        cycle_score = max(largest_ring_size - 6, 0)
        score = log_p - sas_score - cycle_score
        return score, [score]


class CLScoreEvaluationStrategy(EvaluationStrategy):
    """
    Evaluation of CLscore (Bühlmann, Sven, et Jean-Louis Reymond. « ChEMBL-Likeness Score and Database GDBChEMBL ».
    Frontiers in Chemistry 8 (4 février 2020). https://doi.org/10.3389/fchem.2020.00046.)
    Based on https://github.com/reymond-group/GDBChEMBL
    """

    def __init__(self):
        super().__init__()
        self.scores = None
        self.radius = 3
        self.rooted = True
        self.weighted = True
        self.cut_off = 0.0

        # Loading ChEMBL shingles database
        if self.rooted:
            with open(join(os.environ["SHINGLE_LIBS"],
                           "chembl_24_1_shingle_scores_log10_rooted_nchir_min_freq_100.pkl"), "rb") as pyc:
                self.db_shingles = pickle.load(pyc)
        else:
            with open(join(os.environ["SHINGLE_LIBS"],
                           "chembl_24_1_shingle_scores_log10_nrooted_nchir.pkl"), "rb") as pyc:
                self.db_shingles = pickle.load(pyc)

    def keys(self):
        return ["CLScore"]

    def extract_shingles(self, individual):

        qry_shingles = set()

        radius_constr = self.radius + 1

        # Reloading molecule to make it aromatic
        mol = MolFromSmiles(individual.to_aromatic_smiles())

        for atm_idx in range(individual.mol_graph.GetNumAtoms()):
            for N in range(1, radius_constr):
                bonds = AllChem.FindAtomEnvironmentOfRadiusN(mol, N, atm_idx)

                if not bonds:
                    break

                # the reportedly faster method
                atoms = set()
                for bond_id in bonds:
                    bond = mol.GetBondWithIdx(bond_id)
                    atoms.add(bond.GetBeginAtomIdx())
                    atoms.add(bond.GetEndAtomIdx())

                if self.rooted:
                    new_shingle = Chem.rdmolfiles.MolFragmentToSmiles(mol, list(atoms), bonds, 0, 0,
                                                                      False, False, atm_idx, True, False, False)
                else:
                    new_shingle = Chem.rdmolfiles.MolFragmentToSmiles(mol, list(atoms), bonds, 0, 0,
                                                                      False, False, -1, True, False, False)

                qry_shingles.add(new_shingle)

        return qry_shingles

    def evaluate_individual(self, individual, to_replace_idx=None):
        """
        Based on https://github.com/reymond-group/GDBChEMBL
        :param individual:
        :return:
        """

        super().evaluate_individual(individual, to_replace_idx)

        # Extracting shingles
        qry_shingles = self.extract_shingles(individual)

        # calculate shingle count averaged score
        avg_score = 0
        if qry_shingles:
            sum_scores = 0
            # using log10 of shingle frequency
            if self.weighted:
                for shingle in qry_shingles:
                    # if key not present, add 0 per default
                    sum_scores += self.db_shingles.get(shingle, 0)
            # working binary (i.e. if present -> count++ )
            else:
                for shingle in qry_shingles:
                    if shingle in self.db_shingles:
                        sum_scores += 1
            avg_score = sum_scores / len(qry_shingles)

        if self.cut_off == 0.0 or self.cut_off <= avg_score:
            return avg_score, [avg_score]


class SAScoreEvaluationStrategy(EvaluationStrategy):
    """
    Evaluation of SAScore.
    Ertl, Peter, et Ansgar Schuffenhauer. « Estimation of synthetic accessibility score of drug-like molecules based
    on molecular complexity and fragment contributions ». Journal of Cheminformatics 1, no 1 (10 juin 2009): 8.
    https://doi.org/10.1186/1758-2946-1-8.
    Returning the opposite of the value so that the metric can be maximized
    """

    def __init__(self):
        super().__init__()
        self.scores = None

    def keys(self):
        return ["SAScore"]

    def evaluate_individual(self, individual, to_replace_idx=None):
        super().evaluate_individual(individual, to_replace_idx)

        if individual is None:
            return None
        else:

            mol_graph = MolFromSmiles(individual.to_aromatic_smiles())
            score = sascorer.calculateScore(mol_graph)

            return score, [score]


class NormalizedSAScoreEvaluationStrategy(EvaluationStrategy):
    """
    Evaluation of SAScore.
    Ertl, Peter, et Ansgar Schuffenhauer. « Estimation of synthetic accessibility score of drug-like molecules based
    on molecular complexity and fragment contributions ». Journal of Cheminformatics 1, no 1 (10 juin 2009): 8.
    https://doi.org/10.1186/1758-2946-1-8.
    Returning the normalized [0, 1] SAScore
    """

    def __init__(self):
        super().__init__()
        self.scores = None
        self.sascore_evaluation = SAScoreEvaluationStrategy()

    def keys(self):
        return ["SAScore"]

    def evaluate_individual(self, individual, to_replace_idx=None):

        super().evaluate_individual(individual, to_replace_idx)

        if individual is None:
            return None, [None]
        else:
            unnormalized_sascore, _ = self.sascore_evaluation.evaluate_individual(individual)
            score = 1 - (unnormalized_sascore - 1) / 9

            return score, [score]


class QEDEvaluationStrategy(EvaluationStrategy):
    """
    Evaluation of population with QED score using RDKit implementation.
    (Bickerton, G. Richard, Gaia V. Paolini, Jérémy Besnard, Sorel Muresan, et Andrew L. Hopkins. « Quantifying the
    Chemical Beauty of Drugs ». Nature Chemistry 4, nᵒ 2 (février 2012): 90‑98. https://doi.org/10.1038/nchem.1243.
    """

    def __init__(self):
        super().__init__()
        self.scores = None

    def keys(self):
        return ["qed"]

    def evaluate_individual(self, individual, to_replace_idx=None):

        super().evaluate_individual(individual, to_replace_idx)

        if individual is None:
            return None, [None]
        else:
            mol_graph = MolFromSmiles(individual.to_aromatic_smiles())
            score = qed(mol_graph)
            return score, [score]

#BA
class BAEvaluationStrategy(EvaluationStrategy):

    def __init__(self):
        super().__init__()
        self.scores = None

    def keys(self):
        return ["BA"]

    def evaluate_individual(self, individual, to_replace_idx=None):

        super().evaluate_individual(individual, to_replace_idx)
        if individual is None:
            return None, [None]
        else:
            test_data_path = "input.csv"
            with open(test_data_path, "w") as fw:
                fw.write("molecule\n")
                fw.write(individual.to_aromatic_smiles())

            os.system("chemprop_predict --test_path {test_data_path} --checkpoint_dir {model_path} --preds_path {result_path}".format(test_data_path=test_data_path,
                        model_path="BA_model_2",
                        result_path="output.csv"))

            df = pd.read_csv("output.csv")
            score = -df.iloc[0]["docking score"]

            return score, [score]
        
#MJ        
class MJnetEvaluationStrategy(EvaluationStrategy):
    """
    MJnet score for given...
    """
    def __init__(self):
        super().__init__()
        self.scores = None
        self.koembs = pickle.load(open("mjnet_res/kd_cgs_OD_emb.pkl", "rb"))

    def get_emb(self, smi):
        # print(smi)
        modified = parse.quote(smi)
        response = requests.get("http://163.152.163.111:8079/embedding_mjnet?smiles={}".format(modified))
        response = response.json()
        # print(response.keys())

        emb = response[smi]
        return emb

    def keys(self):
        return ["mjnet"]

    def evaluate_individual(self, individual, to_replace_idx=None):
        super().evaluate_individual(individual, to_replace_idx)

        """
        molemb : drug embedding vector
        koemb : gene expression embedding vector
        """
        if individual is None:
            return None, [None]
        else:
            #koemb = self.koembs["CGS001_A375_96H:KRAS:1"]
            koemb = self.koembs["CGS001_MCF7_96H:KRAS:2"]
            molemb = self.get_emb(str(individual))

            score = sum([a*b for a,b in zip(molemb, koemb)])
        return score, [score]        
        
        
class RDFiltersEvaluationStrategy(EvaluationStrategy):
    """
    Adapted from https://github.com/PatWalters/rd_filters

    MIT License

    Copyright (c) 2018 Patrick Walters

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
    """

    def __init__(self):
        super().__init__()
        self.scores = None
        self.rules_file_name = os.environ["FILTER_RULES_DATA"] + "/rules.json"
        self.alert_file_name = os.environ["FILTER_RULES_DATA"] + "/alert_collection.csv"
        self.rule_df = pd.read_csv(self.alert_file_name)
        # make sure there wasn't a blank line introduced
        self.rule_df = self.rule_df.dropna()
        self.rule_list = []
        with open(self.rules_file_name) as json_file:
            self.rule_dict = json.load(json_file)
        self.rules_list = [x.replace("Rule_", "") for x in self.rule_dict.keys()
                           if x.startswith("Rule") and self.rule_dict[x]]
        self._build_rule_list()

    def keys(self):
        return ["RDFilters"]

    def _build_rule_list(self):
        self.rule_df = self.rule_df[self.rule_df.rule_set_name.isin(self.rules_list)]
        tmp_rule_list = self.rule_df[["rule_id", "smarts", "max", "description"]].values.tolist()
        for rule_id, smarts, max_val, desc in tmp_rule_list:
            smarts_mol = Chem.MolFromSmarts(smarts)
            if smarts_mol:
                self.rule_list.append([smarts_mol, max_val, desc])

    def evaluate_individual(self, individual, to_replace_idx=None):

        super().evaluate_individual(individual, to_replace_idx)

        mol = Chem.MolFromSmiles(individual.to_aromatic_smiles())

        if mol is None:
            return 0, [0]

        desc_list = [Descriptors.MolWt(mol), Descriptors.MolLogP(mol), Descriptors.NumHDonors(mol),
                     Descriptors.NumHAcceptors(mol), Descriptors.TPSA(mol), CalcNumRotatableBonds(mol)]
        df = pd.DataFrame([desc_list], columns=[
            "MW", "LogP", "HBD", "HBA", "TPSA", "Rot"])
        df_ok = df[df.MW.between(*(self.rule_dict["MW"])) &
                   df.LogP.between(*(self.rule_dict["LogP"])) &
                   df.HBD.between(*(self.rule_dict["HBD"])) &
                   df.HBA.between(*(self.rule_dict["HBA"])) &
                   df.TPSA.between(*(self.rule_dict["TPSA"]))]
        if len(df_ok) == 0:
            return 0, [0]
        for row in self.rule_list:
            patt, max_val, _ = row
            if len(mol.GetSubstructMatches(patt)) > max_val:
                return 0, [0]
        return 1, [1]


class EvaluationStrategyComposite(EvaluationStrategyComposant):
    """
    Composite class combining several evaluation strategies
    """

    def __init__(self, evaluation_strategies):
        super().__init__()
        self.evaluation_strategies = evaluation_strategies

    def end_step_population(self, pop):
        for strategy in self.evaluation_strategies:
            strategy.end_step_population(pop)

    def get_additional_population_scores(self):
        d = super().get_additional_population_scores()

        for strategy in self.evaluation_strategies:
            d.update(strategy.get_additional_population_scores())

        return d

    def keys(self):
        strat_keys = []

        for strat in self.evaluation_strategies:
            strat_keys.extend(strat.keys())

        return strat_keys

    def compute_record_scores_init_pop(self, population):
        for strategy in self.evaluation_strategies:
            strategy.compute_record_scores_init_pop(population)

    def record_ind_score(self, idx, new_total_score, new_sub_scores, new_individual):
        # Iterative in-depth search in order to set the previously computed sub-scores for the given individual
        # (given by index). Building ordered list of evaluation strategies.
        stack = [self]
        ordered_strategies = []
        ordered_leaf_strategies = []

        while not len(stack) == 0:

            # Popping current node
            curr_strategy = stack.pop()

            # Inserting current strategy in ordered list
            ordered_strategies.append(curr_strategy)

            # If the current strategy is not a leaf, stacking its contained strategies
            if isinstance(curr_strategy, EvaluationStrategyComposite):

                # Iterating on reverse order to stack left of list last
                for i in range(len(curr_strategy.evaluation_strategies) - 1, -1, -1):
                    stack.append(curr_strategy.evaluation_strategies[i])

        # Saving ordered list of leaf strategies
        for strategy in ordered_strategies:
            if not isinstance(strategy, EvaluationStrategyComposite):
                ordered_leaf_strategies.append(strategy)

        # Recording sub-score values to corresponding leaf strategies
        for i in range(len(new_sub_scores)):
            ordered_leaf_strategies[i].record_ind_score(idx, new_sub_scores[i], None, new_individual)

    def get_population_scores(self):

        scores = None
        sub_scores = []

        # Creating lists of scores all the scores of population for all evaluation strategies
        for i, strategy in enumerate(self.evaluation_strategies):
            curr_strategy_evaluation, curr_strategy_sub_scores = strategy.get_population_scores()

            if i == 0:
                scores = np.full((len(self.evaluation_strategies), len(curr_strategy_evaluation)), np.nan)

            scores[i] = curr_strategy_evaluation
            sub_scores.extend(curr_strategy_sub_scores)

        total_scores = []
        for curr_ind_scores in scores.T:
            total_scores.append(self._compute_total_score(curr_ind_scores))

        return np.array(total_scores), np.array(sub_scores)

    def evaluate_individual(self, individual, to_replace_idx=None):

        super().evaluate_individual(individual, to_replace_idx)

        sub_scores = []
        total_scores = []

        for strategy in self.evaluation_strategies:
            curr_total_score, curr_sub_scores = strategy.evaluate_individual(individual, to_replace_idx)
            total_scores.append(curr_total_score)
            sub_scores.extend(curr_sub_scores)

        # Computing total score
        total_score = self._compute_total_score(np.array(total_scores))

        # Returning the product of scores
        return total_score, np.array(sub_scores)

    @abstractmethod
    def _compute_total_score(self, strat_scores):
        pass


class LinearCombinationEvaluationStrategy(EvaluationStrategyComposite):
    """
    Evaluation of the population with a linear combination of given evaluation strategies.
    The coefficients are given in a list of same size as the number of strategies.
    """

    def __init__(self, evaluation_strategies, coefs):
        super().__init__(evaluation_strategies)
        self.coefs = np.array(coefs)

    def _compute_total_score(self, strat_scores):
        return np.sum(strat_scores * self.coefs, axis=0)


class ProductEvaluationStrategy(EvaluationStrategyComposite):
    """
    Computing the product of the internal evaluation strategies as total score
    """

    def __init__(self, evaluation_strategies):
        super().__init__(evaluation_strategies)

    def _compute_total_score(self, strat_scores):
        return np.prod(strat_scores)


class SigmLinWrapperEvaluationStrategy(EvaluationStrategyComposite):
    """
    Passing the wrapped evaluator through a linear function and a sigmoid. Warning : can only wrap a single objective.
    """

    def __init__(self, evaluation_strategies, a, b, l):
        super().__init__(evaluation_strategies)
        self.a = a
        self.b = b
        self.l = l

    def _compute_total_score(self, strat_scores):
        return 1 / (1 + exp(self.l * (self.a * strat_scores[0] + self.b)))


class ProductSigmLinEvaluationStrategy(EvaluationStrategyComposite):
    """
    Evaluation strategy returning the product of multiple scores after passing them through a linear function and
    a sigmoid function.
    Each score for an individual x is computed as sigm(lin(x)), with specified coefficient.
    """

    def __init__(self, evaluation_strategies, a, b, l):
        """
        Initialization of evaluation strategies and coefficients. All parameters must be lists of the same size.
        :param evaluation_strategies: list of evaluation strategies.
        :param a: list of a coefficients for the linear functions applied to each score in the form ax+b
        :param b: list of b coefficients for the linear functions applied to each score in the form ax+b
        :param l: list of lambda coefficient for the sigmoid functions applied to each score
        """

        super().__init__(evaluation_strategies)

        # Recording parameters
        self.a = a
        self.b = b
        self.l = l

        # Population initialization
        self.scores = None

    def _compute_total_score(self, strat_scores):
        tmp_scores = []

        for i, curr_strat_score in enumerate(strat_scores):
            tmp_scores.append(1 / (1 + exp(self.l[i] * (self.a[i] * curr_strat_score + self.b[i]))))

        print("TMP scores : " + str(tmp_scores))

        return np.prod(tmp_scores)


def scores_to_scores_dict(total_scores, scores, keys):
    # Creation of dictionary containing the scores for each evaluator
    step_scores_dict = {}
    for i, k in enumerate(keys):
        step_scores_dict[k] = scores[i]
    step_scores_dict["total"] = total_scores

    return step_scores_dict


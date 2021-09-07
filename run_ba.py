from evomol import run_model
from evomol.plot_exploration import exploration_graph

run_model({
    "obj_function": {
        "type":"linear_combination",
        "functions":["qed","sascore","BA"],
        "coef":[2,1,1]
        },
    #"obj_function":"norm_sascore",
    "optimization_parameters": {
        "max_steps": 500,
        "pop_max_size":5000,
        #"k_to_replace":100,
        #"mutation_find_improver_tries":
    },
    "io_parameters": {
        #"model_path": "examples/1_qed"
        #"model_path": "examples/2_kras_mcf7"
        "model_path": "examples/QED_SA_BA2coeff211_step500",
        "smiles_list_init":["C"]
    },
})

#"smiles_list_init":["C1CCC(C1)C(CC#N)N2C=C(C=N2)C3=C4C=CNC4=NC=N3",
#                            "CC1CCN(CC1N(C)C2=NC=NC3=C2C=CN3)C(=O)CC#N",
#                            "CNS(=O)(=O)C[C@@H]1CC[C@H](CC1)N(C)c2[nH]cnc3nccc23",
#                            "CCS(=O)(=O)N1CC(C1)(CC#N)N2C=C(C=N2)C3=C4C=CNC4=NC=N3"]

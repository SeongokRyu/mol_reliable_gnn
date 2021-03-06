
import numpy as np
import os
import time
import pickle

import torch
import torch.nn as nn
import torch.nn.functional as F

import torch.optim as optim
from torch.utils.data import DataLoader

from tqdm import tqdm

from nets.molecules_graph_regression.load_net import gnn_model # import all GNNS
from train.metrics import binary_class_perfs


def train_val_pipeline_classification(MODEL_NAME, DATASET_NAME, dataset, config, params, net_params, dirs):

    if params['bbp'] == True:
        from train.train_molecules_graph_classification_bbp import \
            train_epoch_classification, evaluate_network_classification # import train functions
        from nets.molecules_graph_regression.load_bbp_net import gnn_model
    else:
        from train.train_molecules_graph_classification import \
            train_epoch_classification, evaluate_network_classification # import train functions
        from nets.molecules_graph_regression.load_net import gnn_model

    t0 = time.time()
    per_epoch_time = []

    if MODEL_NAME in ['GCN', 'GAT']:
        if net_params['self_loop']:
            print("[!] Adding graph self-loops for GCN/GAT models (central node trick).")
            dataset._add_self_loops()
    
    trainset, valset, testset = dataset.train, dataset.val, dataset.test
    root_ckpt_dir, write_file_name, root_output_dir = dirs
        
    device = net_params['device']
    
    print("Training Graphs: ", len(trainset))
    print("Validation Graphs: ", len(valset))
    print("Test Graphs: ", len(testset))

    model = gnn_model(MODEL_NAME, net_params)
    model = model.to(device)

    #   Choose optmizer
    if params['optimizer'] == 'ADAM':
        optimizer = optim.Adam(model.parameters(), lr=params['init_lr'], weight_decay=params['weight_decay'])
    elif params['optimizer'] == 'SGD':
        optimizer = optim.SGD(model.parameters(), lr=params['init_lr'], weight_decay=params['weight_decay'])
    else:
        raise NameError('No optimizer given')

    #   Choose learning rate scheduler
    if params['scheduler'] == 'step':
        scheduler = optim.lr_scheduler.StepLR(optimizer, 
                                            step_size=params['step_size'], 
                                            gamma=params['lr_reduce_factor'])
    else:
        scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min',
                                                         factor=params['lr_reduce_factor'],
                                                         patience=params['lr_schedule_patience'],
                                                         verbose=True)

    train_loader = DataLoader(trainset, batch_size=params['batch_size'], shuffle=True, collate_fn=dataset.collate)
    val_loader = DataLoader(valset, batch_size=params['batch_size'], shuffle=False, collate_fn=dataset.collate)
    test_loader = DataLoader(testset, batch_size=params['batch_size'], shuffle=False, collate_fn=dataset.collate)
        
    """
        Training / Evaluating
    """
    
    # At any point you can hit Ctrl + C to break out of training early.
    try:
        with tqdm(range(params['epochs'])) as t:
            for epoch in t:

                t.set_description('Epoch %d' % epoch)

                start = time.time()

                epoch_train_loss, epoch_train_perf, optimizer, train_scores, train_targets = \
                    train_epoch_classification(model, optimizer, device, train_loader, epoch, params)
                epoch_val_loss, epoch_val_perf, val_scores, val_targets, val_smiles = \
                    evaluate_network_classification(model, device, val_loader, epoch, params)
                _, epoch_test_perf, test_scores, test_targets, test_smiles = \
                    evaluate_network_classification(model, device, test_loader, epoch, params)        

                t.set_postfix(time=time.time()-start, 
                            lr=optimizer.param_groups[0]['lr'],
                            train_loss=epoch_train_loss, 
                            val_loss=epoch_val_loss,
                            train_AUC=epoch_train_perf['auroc'], 
                            val_AUC=epoch_val_perf['auroc'], 
                            test_AUC=epoch_test_perf['auroc'], 
                            train_ECE=epoch_train_perf['ece'], 
                            val_ECE=epoch_val_perf['ece'],
                            test_ECE=epoch_test_perf['ece'])
                            
                per_epoch_time.append(time.time()-start)


                if params['scheduler'] == 'step':
                    scheduler.step()
                else:
                    scheduler.step(epoch_val_loss)
                    if optimizer.param_groups[0]['lr'] < params['min_lr']:
                        print("\n!! LR EQUAL TO MIN LR SET.")
                        break
                
                # Stop training after params['max_time'] hours
                if time.time()-t0 > params['max_time']*3600:
                    print('-' * 89)
                    print("Max_time for training elapsed {:.2f} hours, so stopping".format(params['max_time']))
                    break
                
    except KeyboardInterrupt:
        print('-' * 89)
        print('Exiting from training early because of KeyboardInterrupt')
    
    # Saving checkpoint
    if config['save_params'] is True:
        ckpt_dir = os.path.join(root_ckpt_dir, "RUN_")
        if not os.path.exists(ckpt_dir):
            os.makedirs(ckpt_dir)
        torch.save(model.state_dict(), '{}.pkl'.format(ckpt_dir  
            + '/seed_' +str(params['seed']) + '_dtseed_' + str(params['data_seed'])+ "_epoch_"+ str(epoch)))

    #   Evaluate train & test set based on trained models

    if params['mcdropout'] == True:
        #   get 30 predicts from different dropout models.
        test_scores_list = []
        test_targets = []
        train_scores_list = []
        train_targets = []

        for i in range(params['mc_eval_num_samples']):

            test_loss, test_perf, test_scores, test_targets, test_smiles = \
                evaluate_network_classification(model, device, test_loader, epoch, params)
            train_loss, train_perf, train_scores, train_targets, train_smiles = \
                evaluate_network_classification(model, device, train_loader, epoch, params)
        
            test_scores_list.append(test_scores.detach().cpu().numpy())
            train_scores_list.append(train_scores.detach().cpu().numpy())

        test_scores = np.mean(test_scores_list, axis=0)
        train_scores = np.mean(train_scores_list, axis=0)

        test_perfs = binary_class_perfs(test_scores, test_targets.detach().cpu().numpy())
        train_perfs = binary_class_perfs(train_scores, train_targets.detach().cpu().numpy())

    else:
        if params['bbp'] == True:
            test_loss, test_perf, test_scores, test_targets, test_smiles = \
                evaluate_network_classification(model, device, test_loader, epoch, params, Nsamples=int(params['bbp_eval_Nsample']))
            train_loss, train_perf, train_scores, train_targets, train_smiles = \
                evaluate_network_classification(model, device, train_loader, epoch, params, Nsamples=int(params['bbp_eval_Nsample']))
        else:
            test_loss, test_perf, test_scores, test_targets, test_smiles = \
                evaluate_network_classification(model, device, test_loader, epoch, params)
            train_loss, train_perf, train_scores, train_targets, train_smiles = \
                evaluate_network_classification(model, device, train_loader, epoch, params)
        test_scores = test_scores.detach().cpu().numpy()
        val_scores= val_scores.detach().cpu().numpy()
        train_scores = train_scores.detach().cpu().numpy()


    #   additional metrics for tox21: accuracy, auc, precision, recall, f1, + ECE

    print("Test AUC: {:.4f}".format(test_perf['auroc']))
    print("Test ECE: {:.4f}".format(test_perf['ece']))
    print("Train AUC: {:.4f}".format(train_perf['auroc']))
    print("Train ECE: {:.4f}".format(train_perf['ece']))
    print("TOTAL TIME TAKEN: {:.4f}s".format(time.time()-t0))
    print("AVG TIME PER EPOCH: {:.4f}s".format(np.mean(per_epoch_time)))

        
    """
        Write the results in out_dir/results folder
    """

    with open(write_file_name + '_seed_' +str(params['seed'])
                + '_dtseed_' +str(params['data_seed']) + '.txt', 'w') as f:
        f.write("""Dataset: {},\nModel: {}\n\nparams={}\n\nnet_params={}\n\n{}\n\nTotal Parameters: {}\n\n
    FINAL RESULTS\nTEST ACC: {:.4f}\nTEST AUROC: {:.4f}\nTEST Precision: {:.4f}\nTEST Recall: {:.4f}\nTEST F1: {:.4f}\nTEST AUPRC: {:.4f}\nTEST ECE: {:.4f}\nTRAIN ACC: {:.4f}\nTRAIN AUROC: {:.4f}\nTRAIN Precision: {:.4f}\nTRAIN Recall: {:.4f}\nTRAIN F1: {:.4f}\nTRAIN AUPRC: {:.4f}\nTRAIN ECE: {:.4f}\n\n
    Total Time Taken: {:.4f} hrs\nAverage Time Per Epoch: {:.4f} s\n\n\n"""\
          .format(DATASET_NAME, MODEL_NAME, params, net_params, model, net_params['total_param'],
                  np.mean(np.array(test_perf['accuracy'])), np.mean(np.array(test_perf['auroc'])),
                  np.mean(np.array(test_perf['precision'])), np.mean(np.array(test_perf['recall'])),
                  np.mean(np.array(test_perf['f1'])), np.mean(np.array(test_perf['auprc'])),
                  np.mean(np.array(test_perf['ece'])),
                  np.mean(np.array(train_perf['accuracy'])), np.mean(np.array(train_perf['auroc'])),
                  np.mean(np.array(train_perf['precision'])), np.mean(np.array(train_perf['recall'])),
                  np.mean(np.array(train_perf['f1'])), np.mean(np.array(train_perf['auprc'])),
                  np.mean(np.array(train_perf['ece'])),
                  (time.time()-t0)/3600, np.mean(per_epoch_time)))

    # Saving predicted outputs

    predictions = {}
    predictions['train_smiles'] = train_smiles
    predictions['train_scores'] = train_scores
    predictions['train_targets'] = train_targets.detach().cpu().numpy()
    predictions['val_smiles'] = val_smiles
    predictions['val_scores'] = val_scores
    predictions['val_targets'] = val_targets.detach().cpu().numpy()
    predictions['test_smiles'] = test_smiles
    predictions['test_scores'] = test_scores
    predictions['test_targets'] = test_targets.detach().cpu().numpy()
    with open('{}.pkl'.format(root_output_dir+ '_seed_' +str(params['seed'])
                + '_dtseed_' +str(params['data_seed'])), 'wb') as f:
        pickle.dump(predictions, f)


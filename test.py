# Tes the model performance

import argparse
import os
import pandas as pd
import numpy as np
import time
import matplotlib.pyplot as plt
import torch
from DataLoader.dataset import BraTSDataset, get_dataloader
from monai.data import DataLoader, decollate_batch
from monai.handlers.utils import from_engine
from monai.metrics import DiceMetric
from utils.general import load_pretrained_model
from utils.meter import AverageMeter

from monai.metrics import DiceMetric
from monai.utils.enums import MetricReduction
from monai.inferers import sliding_window_inference
from monai.networks.nets import SwinUNETR
from monai.transforms import (
    AsDiscrete,
    Activations,
)
from functools import partial

import hydra
from omegaconf import OmegaConf, DictConfig
import logging

# configure logger as INFO
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

file_handler = logging.FileHandler(filename= "logger.log")
stream_handler = logging.StreamHandler()
formatter = logging.Formatter(fmt= "%(asctime)s: %(message)s", datefmt= '%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)

# set as file handler and stream handler
logger.addHandler(file_handler)
logger.addHandler(stream_handler)

# read command line args
def read_args():
    '''command line arguments for setting up 
    neccassary paths and params'''
    parser = argparse.ArgumentParser()
    parser.add_argument('--weights', type = str, default = "", help = "weight \
        file path ")
    parser.add_argument('--fold', default= 0, type= int, 
                        help= "fold number for evaluation")
    parser.add_argument('--workers', type = int, default=2,\
        help = "number of workers")
    parser.add_argument('--batch', type = int, default=1, \
        help= "batch size to load the dataset")
    parser.add_argument('--json_file', type = str, default= "", \
                        help= "path to the data json file")
    parser.add_argument('--platform_changed', action= 'store_true', help= "running on other platfrom")
    opt = parser.parse_args()
    return opt

# run evaluation on the test set
def evaluate(model,
              weights, 
              loader, 
              post_pred = None, 
              post_sigmoid = None,
              acc_func = None,
              model_inferer = None):
    '''
    To evaluate the model performance


    Parameters
    ----------:
    model: nn.Module
    weight: str
    loader: torch.utils.data.Dataset
    post_pred: monai.transforms.post.array.AsDiscrete
    post_sigmoid: monai.transforms.post.array.Activations
    acc_func: monai.metrics.meandice.DiceMetric 
    model_inferer: nn.Module
    '''
    # set cuda
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # load the model and set the model on evaluation mode
    model = load_pretrained_model(model, state_path=weights)
    model.eval()
    model.to(device)
    tic = time.time()
    run_acc = AverageMeter()
    # run evaluation
    with torch.no_grad():
        for index, batch_data in enumerate(loader):
            prediction_lists = decollate_batch(model_inferer(batch_data["image"].to(device)))
            masks = decollate_batch(batch_data["label"].to(device)) 
            predictions = [post_pred(post_sigmoid(prediction)) for prediction in prediction_lists]
            acc_func.reset()
            acc_func(y_pred = predictions, y = masks)
            acc, not_nans = acc_func.aggregate()
            run_acc.update(acc.cpu().numpy(), n = not_nans.cpu().numpy())
            dice_tc = run_acc.avg[0]
            dice_wt = run_acc.avg[1]
            dice_et = run_acc.avg[2]
            print(
                "Val  {}/{}".format(index, len(loader)),
                ", dice_tc:",
                dice_tc,
                ", dice_wt:",
                dice_wt,
                ", dice_et:",
                dice_et,
                ", time {:.2f}s".format(time.time() - tic),
            )
            tic = time.time()
    return run_acc.avg

@hydra.main(config_name='configs', config_path= 'conf', version_base=None)
def main(cfg: DictConfig):
    """
    Function that handles everything..
    """
    # command line args
    args = read_args()

    # choose cuda if available
    device = 'cuda' if torch.cuda.is_available() else 'cpu'

    # if training on colab or other platform, set paths accordingly
    if args.platform_changed:
        data_csv_file_path = cfg.paths.dataset_file
        data_json_file = cfg.paths.json_file
        data_dir = cfg.paths.train_dir
    else:
        data_csv_file_path = cfg.paths.dataset_file
        data_json_file = cfg.paths.json_file
        data_dir = cfg.paths.train_path

    # configure model
    roi = cfg.model.roi
    model =   SwinUNETR(
                    img_size=roi,
                    in_channels=4,
                    out_channels=3,
                    feature_size=48,
                    drop_rate=0.0,
                    attn_drop_rate=0.0,
                    dropout_path_rate=0.0,
                    use_checkpoint=True,
                            ).to(device)
    
    weights = args.weights
    batch_size = args.batch
    wokers = args.workers
    fold = args.fold

    post_pred = AsDiscrete(argmax= False, threshold = 0.5)
    post_sigmoid = Activations(sigmoid= True)
    acc_func =  DiceMetric(include_background=True, reduction=MetricReduction.MEAN_BATCH, 
                                      get_not_nans=True)
    model_inferer = partial(
                        sliding_window_inference,
                        roi_size=[roi] * 3,
                        sw_batch_size=cfg.training.sw_batch_size,
                        predictor=model,
                        overlap=cfg.model.infer_overlap)
    if args.json_file:
        data_json_file = args.json_file
    logging.info('Loading dataset')

    # load test dataset
    test_loader = get_dataloader(BraTSDataset,
                                 path_to_csv= data_csv_file_path,
                                 phase = 'val',
                                 batch_size= batch_size,
                                 num_workers= wokers,
                                 json_file=data_json_file,
                                 fold = fold,
                                 train_dir= data_dir)
    
    logger.info('Successfully loaded. \n')
    logger.info(f'Dataset size: {len(test_loader)}')
    
    logger.info('Evaluate on the test set')

    # evaluate the test set on a trained model
    mean_dice = evaluate(model= model,
             weights=weights,
             loader= test_loader,
             post_pred= post_pred,
             post_sigmoid= post_sigmoid,
             acc_func= acc_func,
             model_inferer= model_inferer)
    
    logger.info(f"Mean dice on the test set: {mean_dice}")


if __name__ == '__main__':
    main()

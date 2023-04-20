from typing import Dict, List, Tuple

import torch
import wandb
import random
import numpy as np
from sklearn.metrics import f1_score

import accelerate

def generate_subsampling(nchunks, samples_per_patch, im_shape, au_shape):
    chunk_idx = random.randint(0, nchunks)

    image_chunk_size = np.prod(im_shape[1:-1]) // nchunks
    audio_chunk_size = au_shape[1] // samples_per_patch // nchunks
    subsampling = {
        'image': torch.arange(
            image_chunk_size * chunk_idx, image_chunk_size * (chunk_idx + 1)),
        'audio': torch.arange(
            audio_chunk_size * chunk_idx, audio_chunk_size * (chunk_idx + 1)),
        'label': None,
    }

    return subsampling

def train_step(model: torch.nn.Module, 
               dataloader: torch.utils.data.DataLoader, 
               loss_fn: torch.nn.Module, 
               optimizer: torch.optim.Optimizer,
               device: torch.device) -> Tuple[float, float]:
    
    model.train()

    train_loss, train_fscore = 0, 0

    for i, batch in enumerate(dataloader):
        batch = {k: batch[k].to(device) for k in batch.keys()}
        subsample = generate_subsampling(32, 16, batch['image'].shape, batch['audio'].shape)
        y_pred = model(inputs=batch, subsampled_output_points=subsample)
        
        y_pred = y_pred['logits']['label']
        y = batch['label']
        loss = loss_fn(y_pred, y)
        train_loss += loss.item() 

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        y_pred_class = torch.argmax(torch.softmax(y_pred, dim=1), dim=1)
        train_fscore += f1_score(y.argmax(dim=1).cpu().numpy(), y_pred_class.cpu().numpy(), average='weighted')

        wandb.log({"batch train loss": loss.item()})

    avg_train_loss = train_loss / len(dataloader)
    avg_train_f1 = train_fscore / len(dataloader)
    return avg_train_loss, avg_train_f1


def val_step(model: torch.nn.Module, 
             dataloader: torch.utils.data.DataLoader, 
             loss_fn: torch.nn.Module, 
             optimizer: torch.optim.Optimizer,
             device: torch.device)-> Tuple[float, float]:
    
        model.eval()
        
        val_loss, val_fscore = 0, 0
        
        with torch.inference_mode():
            for i, batch in enumerate(dataloader):
                batch = {k: batch[k].to(device) for k in batch.keys()}
                subsample = generate_subsampling(32, 16, batch['image'].shape, batch['audio'].shape)
                y_pred = model(inputs=batch, subsampled_output_points=subsample)

                y_pred = y_pred['logits']['label']
                y = batch['label']

                loss = loss_fn(y_pred, y)
                val_loss += loss.item()

                y_pred_class = torch.argmax(torch.softmax(y_pred, dim=1), dim=1)
                val_fscore += f1_score(y.argmax(dim=1).cpu().numpy(), y_pred_class.cpu().numpy(), average='weighted')
        
        avg_val_loss = val_loss / len(dataloader)
        avg_val_f1 = val_fscore / len(dataloader)
        return avg_val_loss, avg_val_f1


def test_step(model: torch.nn.Module, 
              dataloader: torch.utils.data.DataLoader,
              device: torch.device) -> float:
    gold, pred = [], []
    with torch.inference_mode():
        for i, batch in enumerate(dataloader):
            batch = {k: batch[k].to(device) for k in batch.keys()}
            subsample = generate_subsampling(32, 16, batch['image'].shape, batch['audio'].shape)
            test_pred_logits = model(inputs=batch, subsampled_output_points=subsample)

            test_pred_logits = test_pred_logits['logits']['label']

            test_pred_labels = test_pred_logits.argmax(dim=1)
            pred.extend(test_pred_labels.tolist())
            gold.extend(batch['label'].argmax(dim=1).tolist())

    test_acc = f1_score(gold, pred, average='weighted')
    return test_acc

def test_for_eval(model: torch.nn.Module, 
              dataloader: torch.utils.data.DataLoader, 
              device: torch.device) -> float:
    gold, pred = [], []
    with torch.inference_mode():
        for i, batch in enumerate(dataloader):
            batch = {k: batch[k].to(device) for k in batch.keys()}
            subsample = generate_subsampling(32, 16, batch['image'].shape, batch['audio'].shape)
            test_pred_logits = model(inputs=batch, subsampled_output_points=subsample)

            test_pred_logits = test_pred_logits['logits']['label']

            test_pred_labels = test_pred_logits.argmax(dim=1)
            pred.extend(test_pred_labels.tolist())
            gold.extend(batch['label'].argmax(dim=1).tolist())

    test_acc = f1_score(gold, pred, average='weighted')
    return test_acc, gold, pred

def train(model: torch.nn.Module, 
          train_dataloader: torch.utils.data.DataLoader,
          val_dataloader: torch.utils.data.DataLoader,
          optimizer: torch.optim.Optimizer,
          loss_fn: torch.nn.Module,
          epochs: int,
          device: torch.device,
          patience: int) -> None:

    model.to(device)
    
    train_losses, val_losses = [], []
    train_fscores, val_fscores = [], []
    prev_val_score = 0
    no_improv_epochs = 0

    for epoch in range(epochs):
        avg_train_loss, avg_train_f1 = train_step(model=model,
                                          dataloader=train_dataloader,
                                          loss_fn=loss_fn,
                                          optimizer=optimizer,
                                          device=device)

        # EARLY STOPPING CODE
        avg_val_loss, avg_val_f1 = val_step(model=model,
                                          dataloader=val_dataloader,
                                          loss_fn=loss_fn,
                                          optimizer=optimizer,
                                          device=device)
        if avg_val_f1 < prev_val_score:
            no_improv_epochs += 1
        prev_val_score = avg_val_f1

        if no_improv_epochs >= patience:
            return None

        train_losses.append(avg_train_loss)
        train_fscores.append(avg_train_f1)
        val_losses.append(avg_val_loss)
        val_fscores.append(avg_val_f1)
        
        wandb.log({"train loss": avg_train_loss, "val loss": avg_val_loss, 
           "train F1": avg_train_f1, "val F1": avg_val_f1,
           "epoch": epoch})

    return None
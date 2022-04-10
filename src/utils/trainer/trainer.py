# !/usr/bin/env python3
# -*- coding: utf-8 -*-
# @time: 2022/4/8 14:55
# @author: 芜情
# @description: the abstract training or testing process of model
import sys
from typing import Optional

import torch
from torch import Tensor
from torch.utils.data import DataLoader

from nn import EnhancedModule
from nn.QPENet import QPENet
from utils.trainer.TestDataset import TestDataset
from utils.trainer.file_monitor import CheckpointMonitor
from utils.trainer.module_helpers import is_overridden
from utils.trainer.progress_bar import progress_bar


class Trainer(object):

    def __init__(
            self, *,
            max_epoch: int,
            device: str = None,
            to_save: str,
            seed: int = 2022
    ):
        self.max_epoch = max_epoch
        self.device = device if device is not None else "cuda:0" if torch.cuda.is_available() else "cpu"
        self.to_save = to_save

        self.monitor = CheckpointMonitor(src_path=".", dest_path=to_save)

        # fix the seed in order to keep the idempotence
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)

    def fit(
            self,
            model: EnhancedModule,
            train_loader: DataLoader,
            validation_loader: DataLoader,
            ckpt_path: Optional[str] = None
    ):
        start_epoch = 1
        if ckpt_path is not None:
            checkpoint = torch.load(ckpt_path, map_location=self.device)
            model.load_state_dict(checkpoint["state_dict"])
            start_epoch = checkpoint["epoch"] + 1
            model.optimizer.load_state_dict(checkpoint["optimizer"])
            if "lr_scheduler" in checkpoint and model.lr_scheduler is not None:
                model.lr_scheduler.load_state_dict(checkpoint["lr_scheduler"])

        model.train()
        model.to(self.device)
        total_per_epoch = len(train_loader)

        self.monitor.start()

        for epoch in range(start_epoch, self.max_epoch + 1):

            # training loop
            training_epoch_outputs = []

            for batch_index, (inputs, labels) in enumerate(train_loader):
                inputs = inputs.to(self.device)
                labels = labels.to(self.device)

                model.optimizer.zero_grad(set_to_none=True)

                training_outs = model.training_step(inputs, labels)
                training_epoch_outputs.append(training_outs)
                if is_overridden("training_step_end", model):
                    training_outs = model.training_step_end(training_outs)

                if isinstance(training_outs, Tensor):
                    training_outs.backward()
                elif isinstance(training_outs, dict):
                    try:
                        training_outs["loss"].backward()
                    except KeyError:
                        sys.stderr.write("\nif the training outputs is a dictionary, it must has a key named 'loss'.\n")
                else:
                    raise TypeError(f"\nthe training_outputs [{training_outs}] is unable to backward().\n")

                # update the optimizer
                model.optimizer_step()

                # update the learning rate
                model.lr_scheduler_step()

                sys.stdout.write(f"\r\33[36mEpoch {epoch:06d} {progress_bar(batch_index + 1, total_per_epoch)}\33[0m")
                sys.stdout.flush()

            if is_overridden("training_epoch_end", model):
                model.training_epoch_end(training_epoch_outputs)
            training_epoch_outputs.clear()

            # validation loop
            validation_epoch_outputs = []
            loss_list = []

            for inputs, labels in validation_loader:
                inputs = inputs.to(self.device)
                labels = labels.to(self.device)

                validation_outs = model.validation_step(inputs, labels)
                validation_epoch_outputs.append(validation_outs)
                if is_overridden("validation_step_end", model):
                    validation_outs = model.validation_step_end(validation_outs)

                if isinstance(validation_outs, Tensor):
                    loss_list.append(validation_outs.detach().cpu().item())
                elif isinstance(validation_outs, dict):
                    try:
                        loss_list.append(validation_outs["loss"].detach().cpu().item())
                    except KeyError:
                        sys.stderr.write(
                            "\nif the validation outputs is a dictionary, it must has a key named 'loss'.\n")
                else:
                    raise TypeError(f"\nthe validation_outs [{validation_outs}] is unable to compute the loss.\n")

            if is_overridden("validation_epoch_end", model):
                model.validation_epoch_end(validation_epoch_outputs)
            validation_epoch_outputs.clear()

            mean_loss = sum(loss_list) / len(loss_list)

            sys.stdout.write(
                f"\r\33[36mEpoch {epoch:06d} {progress_bar(1, 1)} loss={mean_loss:.6f}\33[0m"
            )
            sys.stdout.flush()

            print()  # just wrap around for the log information of each epoch shows in different lines

            # save epoch results
            states_dict = {
                "epoch": epoch,
                "state_dict": model.state_dict(),
                "optimizer": model.optimizer.state_dict()
            }
            if model.lr_scheduler is not None:
                states_dict["lr_scheduler"] = model.lr_scheduler.state_dict()

            torch.save(obj=states_dict, f=f"checkpoint_{epoch:06d}_{mean_loss:.6f}_temp.pth")

        self.monitor.stop()

        # clear the GPU memery
        torch.cuda.empty_cache()

    def predict(
            self,
            model: EnhancedModule,
            test_loader: DataLoader,
            ckpt_path: str
    ):
        try:
            checkpoint = torch.load(ckpt_path, map_location=self.device)
            model.load_state_dict(checkpoint["state_dict"])
        except FileNotFoundError:
            sys.stderr.write(f"\nthe file {ckpt_path} does not exist.\n")
        except IOError:
            sys.stderr.write(
                f"\nthere is some wrong when loading parameters, please ensure {ckpt_path} is a right path.\n")

        model.eval()
        model.to(self.device)

        # test loop
        total_outputs = []
        total_per_epoch = len(train_loader)
        for batch_index, (inputs, labels) in enumerate(test_loader):
            inputs = inputs.to(self.device)
            labels = labels.to(self.device)

            outputs = model.predict_step(inputs, labels)
            total_outputs.append(outputs)

            sys.stdout.write(f"\r\33[94m正在处理 {progress_bar(batch_index + 1, total_per_epoch)}\33[0m")
            sys.stdout.flush()

        prediction = torch.cat(total_outputs, dim=0).detach().cpu()

        torch.save(prediction, f=self.to_save + "/prediction.pth")

        sys.stdout.write(f"\r\33[94m处理完毕 {progress_bar(total_per_epoch, total_per_epoch)}\33[0m")


if __name__ == '__main__':
    net = QPENet()
    dataset = TestDataset("qpe")
    train_loader = DataLoader(dataset, batch_size=4)
    validation_loader = DataLoader(dataset, batch_size=3)

    trainer = Trainer(max_epoch=3, to_save="../data")

    # trainer.fit(net, train_loader=train_loader, validation_loader=validation_loader)

    trainer.predict(model=net, test_loader=train_loader, ckpt_path="../data/checkpoint_000003_0.085735_temp.pth")

from .hparams import HParams

import pytorch_lightning as lightning
from pytorch_lightning.profiler import Profiler
from pytorch_lightning.loggers import TensorBoardLogger

import torch
from torch.utils.data import DataLoader, Dataset, IterableDataset

from lightning_trainable import utils
from lightning_trainable.callbacks import EpochProgressBar


class TrainableHParams(HParams):
    # name of the loss, your `compute_metrics` should return a dict with this name in its keys
    loss: str = "loss"

    accelerator: str = "gpu"
    devices: int = 1
    max_epochs: int | None
    max_steps: int = -1
    optimizer: str | dict | None = "adam"
    lr_scheduler: str | dict | None = None
    batch_size: int
    accumulate_batches: int | None = None
    track_grad_norm: int | None = 2
    gradient_clip: float | int | None = None
    profiler: str | Profiler | None = None
    num_workers: int = 4


class Trainable(lightning.LightningModule):
    def __init__(
            self,
            hparams: TrainableHParams | dict,
            train_data: Dataset = None,
            val_data: Dataset = None,
            test_data: Dataset = None
    ):
        super().__init__()
        if not isinstance(hparams, TrainableHParams):
            hparams = TrainableHParams(**hparams)
        self.save_hyperparameters(hparams)

        self.train_data = train_data
        self.val_data = val_data
        self.test_data = test_data

    def compute_metrics(self, batch, batch_idx) -> dict:
        """ Compute any relevant metrics, including the loss, on the given batch """
        raise NotImplementedError

    def training_step(self, batch, batch_idx):
        print(list(self.trainer.callbacks))
        metrics = self.compute_metrics(batch, batch_idx)
        if self.hparams.loss not in metrics:
            raise RuntimeError(f"You must return the loss '{self.hparams.loss}' from `compute_metrics`.")

        for key, value in metrics.items():
            self.log(f"training/{key}", value)

        return metrics[self.hparams.loss]

    def validation_step(self, batch, batch_idx):
        metrics = self.compute_metrics(batch, batch_idx)
        for key, value in metrics.items():
            self.log(f"validation/{key}", value)

    def test_step(self, batch, batch_idx):
        metrics = self.compute_metrics(batch, batch_idx)
        for key, value in metrics.items():
            self.log(f"test/{key}", value)

    def configure_lr_schedulers(self, optimizer):
        """
        Configure LR Schedulers for Lightning
        """
        match self.hparams.lr_scheduler:
            case str() as name:
                kwargs = dict()
                scheduler = utils.get_scheduler(name)(optimizer, **kwargs)
                return dict(
                    scheduler=scheduler,
                    interval="step",
                )
            case dict() as kwargs:
                name = kwargs.pop("name")
                interval = "step"
                if "interval" in kwargs:
                    interval = kwargs.pop("interval")
                scheduler = utils.get_scheduler(name)(optimizer, **kwargs)
                return dict(
                    scheduler=scheduler,
                    interval=interval,
                )
            case type(torch.optim.lr_scheduler._LRScheduler) as Scheduler:
                kwargs = dict()
                interval = "step"
                scheduler = Scheduler(optimizer, **kwargs)
                return dict(
                    scheduler=scheduler,
                    interval="step",
                )
            case (torch.optim.lr_scheduler._LRScheduler() | torch.optim.lr_scheduler.ReduceLROnPlateau()) as scheduler:
                return dict(
                    scheduler=scheduler,
                    interval="step",
                )
            case None:
                # do not use a scheduler
                return None
            case other:
                raise NotImplementedError(f"Unrecognized Scheduler: {other}")

    def configure_optimizers(self):
        """
        Configure optimizers for Lightning
        """
        kwargs = dict()

        match self.hparams.optimizer:
            case str() as name:
                optimizer = utils.get_optimizer(name)(self.parameters(), **kwargs)
            case dict() as kwargs:
                name = kwargs.pop("name")
                optimizer = utils.get_optimizer(name)(self.parameters(), **kwargs)
            case type(torch.optim.Optimizer) as Optimizer:
                optimizer = Optimizer(self.parameters(), **kwargs)
            case torch.optim.Optimizer() as optimizer:
                pass
            case None:
                return None
            case other:
                raise NotImplementedError(f"Unrecognized Optimizer: {other}")

        lr_scheduler = self.configure_lr_schedulers(optimizer)

        if lr_scheduler is None:
            return optimizer

        return dict(
            optimizer=optimizer,
            lr_scheduler=lr_scheduler,
        )

    def configure_callbacks(self):
        """
        Configure and return train callbacks for Lightning
        """
        return [
            lightning.callbacks.ModelCheckpoint(
                monitor=f"validation/{self.hparams.loss}",
                save_last=True,
                every_n_epochs=25,
                save_top_k=5
            ),
            lightning.callbacks.LearningRateMonitor(),
            EpochProgressBar(),
        ]

    def train_dataloader(self):
        """
        Configure and return the train dataloader
        """
        return DataLoader(
            dataset=self.train_data,
            batch_size=self.hparams.batch_size,
            shuffle=not isinstance(self.train_data, IterableDataset),
            pin_memory=True,
            num_workers=self.hparams.num_workers,
        )

    def val_dataloader(self):
        """
        Configure and return the validation dataloader
        """
        return DataLoader(
            dataset=self.val_data,
            batch_size=self.hparams.batch_size,
            shuffle=False,
            pin_memory=True,
            num_workers=self.hparams.num_workers,
        )

    def test_dataloader(self):
        """
        Configure and return the test dataloader
        """
        return DataLoader(
            dataset=self.test_data,
            batch_size=self.hparams.batch_size,
            shuffle=False,
            pin_memory=True,
            num_workers=self.hparams.num_workers,
        )

    def configure_logger(self, **kwargs):
        """
        Configure and return the Logger to be used by the Lightning.Trainer
        """
        kwargs.setdefault("save_dir", "lightning_logs")
        return TensorBoardLogger(
            default_hp_metric=False,
            **kwargs
        )

    def configure_trainer(self, logger_kwargs: dict = None, trainer_kwargs: dict = None):
        """
        Configure and return the Trainer used to train this module
        """
        if logger_kwargs is None:
            logger_kwargs = dict()
        if trainer_kwargs is None:
            trainer_kwargs = dict()

        return lightning.Trainer(
            accelerator=self.hparams.accelerator.lower(),
            logger=self.configure_logger(**logger_kwargs),
            devices=self.hparams.devices,
            max_epochs=self.hparams.max_epochs,
            max_steps=self.hparams.max_steps,
            gradient_clip_val=self.hparams.gradient_clip,
            accumulate_grad_batches=self.hparams.accumulate_batches,
            track_grad_norm=self.hparams.track_grad_norm,
            profiler=self.hparams.profiler,
            benchmark=True,
            **trainer_kwargs,
        )

    @torch.enable_grad()
    def fit(self, logger_kwargs: dict = None, trainer_kwargs: dict = None) -> dict:
        """ Fit the module to data and return validation metrics """
        if logger_kwargs is None:
            logger_kwargs = dict()
        if trainer_kwargs is None:
            trainer_kwargs = dict()

        trainer = self.configure_trainer(logger_kwargs, trainer_kwargs)
        metrics = trainer.validate(self)[0]
        trainer.logger.log_hyperparams(self.hparams, metrics)
        trainer.fit(self)

        return trainer.validate(self)[0]

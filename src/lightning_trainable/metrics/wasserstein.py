
import torch
from torch import Tensor


def wasserstein(x: Tensor, y: Tensor, cost: Tensor = None, epsilon: int | float = 0.1, steps: int = 100) -> Tensor:
    """
    Compute the Wasserstein distance between two distributions.
    @param x: Samples from the first distribution.
    @param y: Samples from the second distribution.
    @param cost: Optional cost matrix. If not provided, the L2 distance is used.
    @param epsilon: The entropic regularization parameter.
    @param steps: The number of Sinkhorn iterations.
    """
    if cost is None:
        cost = x[:, None] - y[None, :]
        cost = torch.flatten(cost, start_dim=2)
        cost = torch.linalg.norm(cost, dim=-1)

    if cost.shape != (x.shape[0], y.shape[0]):
        raise ValueError(f"Expected cost matrix of shape {(x.shape[0], y.shape[0])}, but got {cost.shape}.")

    u = torch.zeros(x.shape[0], device=x.device)
    v = torch.zeros(y.shape[0], device=y.device)

    for step in range(steps):
        u = epsilon * torch.logsumexp(-cost + v[None, :] / epsilon, dim=1)
        v = epsilon * torch.logsumexp(-cost + u[:, None] / epsilon, dim=0)

    w = torch.sum(u * torch.sum(cost * torch.exp(-(u[:, None] + v[None, :]) / epsilon), dim=1), dim=0)

    return w

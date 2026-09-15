# =============================================================================
# OpenAlgo Reinforcement Learning: Asymmetric Loss Penalty Reward Engine
# Heavily penalizes false breakouts, chop whipsaws, and adverse excursions
# =============================================================================

import numpy as np
from typing import Dict, Any

class RLRewardEngine:
    """
    Computes asymmetric reinforcement learning rewards.
    Suppresses loss-making trades and choppy whipsaws via quadratic drawdown penalties.
    """

    def __init__(
        self,
        loss_aversion_lambda: float = 1.8,
        drawdown_penalty_lambda: float = 2.5,
        friction_cost_inr: float = 80.0     # Statutory & slippage per round-trip trade
    ):
        self.lambda_loss = loss_aversion_lambda
        self.lambda_dd = drawdown_penalty_lambda
        self.friction_cost = friction_cost_inr

    def compute_step_reward(
        self,
        delta_pnl_inr: float,
        max_adverse_excursion_inr: float,
        is_terminal_trade: bool = False,
        traded_lot_size: int = 1
    ) -> float:
        """
        Computes the RL reward for a transition step.
        - delta_pnl_inr: PnL change since last bar.
        - max_adverse_excursion_inr: Peak intra-trade unrealized loss (positive number).
        - is_terminal_trade: True if trade closed on this bar.
        """
        reward = delta_pnl_inr / 1000.0  # Scale to standard RL range

        # 1. Asymmetric Loss Penalty (Losses sting 1.8x harder than equivalent gains)
        if delta_pnl_inr < 0:
            reward -= self.lambda_loss * abs(delta_pnl_inr) / 1000.0

        # 2. Quadratic Adverse Excursion Penalty (Punishes violent whipsaw drawdowns)
        norm_dd = max_adverse_excursion_inr / 1000.0
        if norm_dd > 0.5:
            reward -= self.lambda_dd * (norm_dd ** 2)

        # 3. Transaction Friction Penalty on exit
        # 4. Reward Clipping (Prevents gradient explosion / policy saturation on huge anomalies)
        return float(np.clip(reward, -2.5, 2.5))

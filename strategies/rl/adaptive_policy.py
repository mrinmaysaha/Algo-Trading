# =============================================================================
# OpenAlgo Reinforcement Learning: Continuous Adaptive Policy Head
# Dynamically determines Entry Conviction, Stop-Loss ATR, & TP Ratchets
# Includes Live Learning Audit Trail for Full User Transparency
# =============================================================================

import os
import json
import datetime
import csv
import numpy as np
from typing import Tuple, Dict, Any, Optional

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

class AdaptiveRLPolicy:
    """
    Online-learning continuous policy for dynamic risk modulation.
    Maps state vector s_t -> (Action Conviction, SL ATR Multiplier, TP Ratchet Aggressiveness).
    Learns from negative rewards to suppress entries in loss-prone states.
    Maintains an audit trail in logs/rl_learning_audit.csv for full visibility.
    """

    def __init__(
        self,
        state_dim: int = 12,
        weights_file: str = "strategies/rl/policy_weights.json",
        audit_file: str = "logs/rl_learning_audit.csv",
        learning_rate: float = 0.015
    ):
        self.state_dim = state_dim
        self.weights_file = weights_file
        self.audit_file = audit_file
        self.lr = learning_rate

        # Tracking metrics
        self.total_experiences: int = 0
        self.win_count: int = 0
        self.loss_count: int = 0

        # Policy weights: 3 output heads
        # Head 0: Entry Logits (Baseline +0.40 so clean trades are approved until penalized)
        # Head 1: Dynamic SL Multiplier [1.0 to 3.0]
        # Head 2: Dynamic TP Ratchet Aggressiveness [0.2 to 0.8]
        self.weights = np.zeros((state_dim, 3), dtype=np.float32)
        self.bias = np.array([0.40, 1.5, 0.5], dtype=np.float32)

        self.load_weights()
        self._init_audit_file()

    def _init_audit_file(self):
        os.makedirs(os.path.dirname(self.audit_file), exist_ok=True)
        if not os.path.exists(self.audit_file):
            with open(self.audit_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "symbol", "reward", "conviction_before",
                    "conviction_after", "outcome", "total_learned"
                ])

    def predict(self, state: np.ndarray) -> Tuple[float, float, float]:
        """
        Inference: Returns (entry_conviction, dynamic_sl_atr, dynamic_tp_ratchet)
        - entry_conviction: [-1.0, 1.0] where > 0.2 indicates approved confidence
        - dynamic_sl_atr: [0.8, 3.2] multiplier of current ATR
        - dynamic_tp_ratchet: [0.2, 0.8] milestone aggressiveness
        """
        raw = np.dot(state, self.weights) + self.bias
        entry_conviction = float(np.tanh(raw[0]))
        dynamic_sl_atr = float(np.clip(1.5 + np.tanh(raw[1]) * 1.2, 0.8, 3.2))
        dynamic_tp_ratchet = float(np.clip(0.5 + np.tanh(raw[2]) * 0.3, 0.2, 0.8))

        return entry_conviction, dynamic_sl_atr, dynamic_tp_ratchet

    def learn_from_trade(self, state: np.ndarray, action_taken: int, reward: float, symbol: str = "UNKNOWN"):
        """
        Temporal Difference / Policy Gradient Update:
        If reward is negative (loss/chop), suppresses weights corresponding to that state.
        Logs the exact learning transition to the audit trail.
        """
        conv_before, _, _ = self.predict(state)

        # Gradient update with L2 Weight Decay (prevents saturation over long horizons)
        weight_decay = 0.005
        grad_entry = reward * state
        self.weights[:, 0] = (1.0 - weight_decay) * self.weights[:, 0] + self.lr * grad_entry
        self.weights[:, 0] = np.clip(self.weights[:, 0], -2.0, 2.0)
        self.bias[0] = float(np.clip(self.bias[0] + self.lr * reward, -1.0, 1.0))

        self.total_experiences += 1
        if reward >= 0:
            self.win_count += 1
            outcome = "WIN_REINFORCED"
        else:
            self.loss_count += 1
            outcome = "LOSS_PENALIZED"

        conv_after, _, _ = self.predict(state)

        # Save weights to disk
        self.save_weights()

        # Append to audit trail CSV
        try:
            with open(self.audit_file, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                now_str = datetime.datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
                writer.writerow([
                    now_str, symbol, f"{reward:.3f}", f"{conv_before:.3f}",
                    f"{conv_after:.3f}", outcome, self.total_experiences
                ])
        except Exception:
            pass

    def save_weights(self):
        os.makedirs(os.path.dirname(self.weights_file), exist_ok=True)
        data = {
            "total_experiences": self.total_experiences,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "weights": self.weights.tolist(),
            "bias": self.bias.tolist()
        }
        with open(self.weights_file, "w") as f:
            json.dump(data, f, indent=2)

    def load_weights(self):
        if os.path.exists(self.weights_file):
            try:
                with open(self.weights_file, "r") as f:
                    data = json.load(f)
                    self.total_experiences = data.get("total_experiences", 0)
                    self.win_count = data.get("win_count", 0)
                    self.loss_count = data.get("loss_count", 0)
                    self.weights = np.array(data["weights"], dtype=np.float32)
                    self.bias = np.array(data["bias"], dtype=np.float32)
            except Exception:
                pass

"""numpyro model and NUTS inference (doc 07 §3, §5 primary path). Optional dependency: ``pip install .[bayes]``."""
from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt

from .structure import TAU_M_SCALE, U_MAX, U_SD, Structure, log_likelihood


def available() -> bool:
    try:
        import jax  # noqa: F401
        import numpyro  # noqa: F401
    except ImportError:
        return False
    return True


def make_model(S: Structure) -> Any:
    import jax.numpy as jnp
    import numpyro
    import numpyro.distributions as dist

    E0 = len(S.effects) - S.n_delta
    G, L = len(S.comp_groups), len(S.latent_idx)
    mu, sd = jnp.asarray(S.mu[:E0]), jnp.asarray(S.sd[:E0])

    def model() -> None:
        eff = numpyro.sample("eff", dist.Normal(mu, sd).to_event(1))
        if S.n_delta:
            tau = numpyro.sample("tau_m", dist.HalfNormal(TAU_M_SCALE))
            z = numpyro.sample("z_delta", dist.Normal(0.0, 1.0).expand([S.n_delta]).to_event(1))
            eff = jnp.concatenate([eff, tau * z])          # non-centred manufacturer effects
        sm = numpyro.sample("sigma_model", dist.HalfNormal(0.08))
        sc = (numpyro.sample("sigma_comp", dist.HalfNormal(0.15).expand([G]).to_event(1)) if G
              else jnp.zeros(0))
        u = (numpyro.sample("u", dist.TruncatedNormal(0.0, U_SD, low=0.0, high=U_MAX).expand([L]).to_event(1))
             if L else jnp.zeros(0))
        numpyro.factor("loglik", log_likelihood(jnp, S, eff, sm, sc, u))

    return model


def fit_nuts(S: Structure, seed: int = 0, num_warmup: int = 1000, num_samples: int = 1000, num_chains: int = 4,
             target_accept: float = 0.8) -> tuple[dict[str, npt.NDArray[np.float64]], dict[str, Any]]:
    """NUTS, 4 chains x 1000 warm-up x 1000 samples by default. Returns (draws by name, diagnostics).

    Draws are flattened over chains; diagnostics include R-hat, ESS and divergences. If divergences occur the
    run is repeated once with target_accept = 0.95 (doc 07 §5) and both counts are reported.
    """
    import jax
    import numpyro
    from numpyro.infer import MCMC, NUTS

    numpyro.enable_x64()

    def run(ta: float) -> tuple[Any, int]:
        mcmc = MCMC(NUTS(make_model(S), target_accept_prob=ta), num_warmup=num_warmup, num_samples=num_samples,
                    num_chains=num_chains, chain_method="sequential", progress_bar=False)
        mcmc.run(jax.random.PRNGKey(seed), extra_fields=("diverging",))
        return mcmc, int(np.asarray(mcmc.get_extra_fields()["diverging"]).sum())

    mcmc, div = run(target_accept)
    diag: dict[str, Any] = {"engine": "numpyro_nuts", "target_accept": target_accept, "divergences": div,
                            "num_chains": num_chains, "num_warmup": num_warmup, "num_samples": num_samples}
    if div:
        mcmc, div2 = run(0.95)
        diag.update({"divergences_first_run": div, "target_accept": 0.95, "divergences": div2})
    grouped = {k: np.asarray(v) for k, v in mcmc.get_samples(group_by_chain=True).items()}
    named_chains = _name_draws(S, grouped)
    diag.update(_rhat_ess(named_chains))
    return {k: v.reshape(-1) for k, v in named_chains.items()}, diag


def _name_draws(S: Structure, g: dict[str, npt.NDArray[np.float64]]) -> dict[str, npt.NDArray[np.float64]]:
    E0 = len(S.effects) - S.n_delta
    out: dict[str, npt.NDArray[np.float64]] = {}
    for i, name in enumerate(S.effects[:E0]):
        out[name] = g["eff"][..., i]
    if S.n_delta:
        out["tau_m"] = g["tau_m"]
        for i, name in enumerate(S.effects[E0:]):
            out[name] = g["tau_m"] * g["z_delta"][..., i]
    out["sigma_model"] = g["sigma_model"]
    for i, grp in enumerate(S.comp_groups):
        out[f"sigma_comp_{grp}"] = g["sigma_comp"][..., i]
    for i, j in enumerate(S.latent_idx):
        out[f"u_{S.fm.ids[j]}"] = g["u"][..., i]
    return out


def _rhat_ess(chains: dict[str, npt.NDArray[np.float64]]) -> dict[str, Any]:
    from numpyro.diagnostics import effective_sample_size, split_gelman_rubin
    rhat = {k: float(split_gelman_rubin(v)) for k, v in chains.items()}
    ess = {k: float(effective_sample_size(v)) for k, v in chains.items()}
    return {"rhat_max": max(rhat.values()), "ess_min": min(ess.values()), "rhat": rhat, "ess": ess,
            "rhat_ok": max(rhat.values()) < 1.01, "ess_ok": min(ess.values()) > 400}

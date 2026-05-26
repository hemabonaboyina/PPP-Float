"""
lambda_ils.py
=============
Python / NumPy faithful translation of lambda.c from PPP_AR-master/src/LibGnss.

Original C code:
    Copyright (C) 2007-2008 by T.TAKASU, All rights reserved.

References:
    [1] P.J.G.Teunissen, The least-square ambiguity decorrelation adjustment:
        a method for fast GPS ambiguity estimation, J.Geodesy, Vol.70, 65-82, 1995
    [2] X.-W.Chang, X.Yang, T.Zhou, MLAMBDA: A modified LAMBDA method for
        integer least-squares estimation, J.Geodesy, Vol.79, 552-565, 2005

Public API
----------
lambda_py(a, Q, m=2) -> (F, s, info)
    Full LAMBDA: LD factorisation + reduction + mlambda search.
    a : (n,) float ambiguity vector
    Q : (n,n) covariance matrix
    m : number of candidates to return (default 2 for ratio test)
    Returns:
        F    : (n, m) fixed solutions, best first (lowest residual)
        s    : (m,)  sum-of-squared residuals for each candidate
        info : 0 = ok, -1 = LD error, -2 = search loop overflow

lambda_reduction_py(Q) -> (Z, info)
    LAMBDA decorrelation only (returns Z-transform matrix).

lambda_search_py(a, Q, m=2) -> (F, s, info)
    mlambda search only (no reduction step).
"""

from __future__ import annotations
import math
import numpy as np
from typing import Tuple

LOOPMAX     = 10000   # maximum iterations in search loop
MIN_AMB_RES = 3       # minimum ambiguities for ILS-AR


def _sgn(x: float) -> float:
    return -1.0 if x <= 0.0 else 1.0


def _round(x: float) -> float:
    return math.floor(x + 0.5)


# ---------------------------------------------------------------------------
# LD factorisation:  Q = L' * diag(D) * L
# Direct translation of LD() in lambda.c.
# C uses column-major A[i + j*n]; here A[i,j] — same arithmetic.
# ---------------------------------------------------------------------------
def _LD(n: int, Q: np.ndarray) -> Tuple[np.ndarray, np.ndarray, int]:
    """LD factorisation of symmetric PD matrix Q.

    Returns (L, D, info):
        L    : (n,n) — L[i,j] for i>=j, lower-triangular factor
        D    : (n,)  — diagonal
        info : 0=ok, -1=non-positive diagonal (not PD)
    """
    A = Q.copy().astype(float)
    L = np.zeros((n, n))
    D = np.zeros(n)
    info = 0

    for i in range(n - 1, -1, -1):
        D[i] = A[i, i]
        if D[i] <= 0.0:
            info = -1
            break
        a = math.sqrt(D[i])
        for j in range(i + 1):
            L[i, j] = A[i, j] / a
        for j in range(i):
            for k in range(j + 1):
                A[j, k] -= L[i, k] * L[i, j]
        for j in range(i + 1):
            L[i, j] /= L[i, i]

    return L, D, info


# ---------------------------------------------------------------------------
# Integer Gauss transformation  — gauss() in lambda.c
# ---------------------------------------------------------------------------
def _gauss(n: int, L: np.ndarray, Z: np.ndarray, i: int, j: int) -> None:
    mu = int(_round(L[i, j]))
    if mu != 0:
        for k in range(i, n):
            L[k, j] -= float(mu) * L[k, i]
        for k in range(n):
            Z[k, j] -= float(mu) * Z[k, i]


# ---------------------------------------------------------------------------
# Permutation step  — perm() in lambda.c
# ---------------------------------------------------------------------------
def _perm(n: int, L: np.ndarray, D: np.ndarray,
          j: int, del_: float, Z: np.ndarray) -> None:
    eta = D[j] / del_
    lam = D[j + 1] * L[j + 1, j] / del_
    D[j]     = eta * D[j + 1]
    D[j + 1] = del_
    for k in range(j):
        a0 = L[j,     k]
        a1 = L[j + 1, k]
        L[j,     k] = -L[j + 1, j] * a0 + a1
        L[j + 1, k] =  eta * a0 + lam * a1
    L[j + 1, j] = lam
    for k in range(j + 2, n):
        L[k, j], L[k, j + 1] = L[k, j + 1], L[k, j]
    for k in range(n):
        Z[k, j], Z[k, j + 1] = Z[k, j + 1], Z[k, j]


# ---------------------------------------------------------------------------
# LAMBDA reduction  — reduction() in lambda.c
# ---------------------------------------------------------------------------
def _reduction(n: int, L: np.ndarray, D: np.ndarray, Z: np.ndarray) -> None:
    j = n - 2
    k = n - 2
    while j >= 0:
        if j <= k:
            for i in range(j + 1, n):
                _gauss(n, L, Z, i, j)
        del_ = D[j] + L[j + 1, j] ** 2 * D[j + 1]
        if del_ + 1e-6 < D[j + 1]:      # Schnorr-Euchner condition
            _perm(n, L, D, j, del_, Z)
            k = j
            j = n - 2
        else:
            j -= 1


# ---------------------------------------------------------------------------
# mlambda search  — search() in lambda.c  (ref. [2])
# ---------------------------------------------------------------------------
def _search(n: int, m: int, L: np.ndarray, D: np.ndarray,
            zs: np.ndarray) -> Tuple[np.ndarray, np.ndarray, int]:
    """mlambda tree search in decorrelated space.

    Returns (zn, s, info):
        zn   : (n, m) integer candidates (unsorted)
        s    : (m,)   residual sums (unsorted)
        info : 0=ok, -2=loop overflow
    """
    S    = np.zeros((n, n))
    dist = np.zeros(n)
    zb   = np.zeros(n)
    z    = np.zeros(n)
    step = np.zeros(n)
    zn   = np.zeros((n, m))
    s    = np.full(m, 1e99)

    k = n - 1
    dist[k] = 0.0
    zb[k]   = zs[k]
    z[k]    = _round(zb[k])
    y       = zb[k] - z[k]
    step[k] = _sgn(y)

    nn   = 0
    imax = 0
    maxdist = 1e99

    c = 0
    for c in range(LOOPMAX):
        newdist = dist[k] + y * y / D[k]
        if newdist < maxdist:
            # Case 1: move down the tree
            if k != 0:
                k -= 1
                dist[k] = newdist
                for i in range(k + 1):
                    S[k, i] = S[k + 1, i] + (z[k + 1] - zb[k + 1]) * L[k + 1, i]
                zb[k]   = zs[k] + S[k, k]
                z[k]    = _round(zb[k])
                y       = zb[k] - z[k]
                step[k] = _sgn(y)
            # Case 2: leaf — store candidate
            else:
                if nn < m:
                    if nn == 0 or newdist > s[imax]:
                        imax = nn
                    zn[:, nn] = z.copy()
                    s[nn]     = newdist
                    nn += 1
                else:
                    if newdist < s[imax]:
                        zn[:, imax] = z.copy()
                        s[imax]     = newdist
                        imax = int(np.argmax(s))
                    maxdist = s[imax]
                z[0]    += step[0]
                y        = zb[0] - z[0]
                step[0]  = -step[0] - _sgn(step[0])
        # Case 3: move up or exit
        else:
            if k == n - 1:
                break
            else:
                k      += 1
                z[k]   += step[k]
                y       = zb[k] - z[k]
                step[k] = -step[k] - _sgn(step[k])

    # Sort candidates by residual s ascending (best = index 0)
    valid = min(nn, m)
    if valid > 1:
        order     = np.argsort(s[:valid])
        zn[:, :valid] = zn[:, order]
        s[:valid]     = s[order]

    info = -2 if c >= LOOPMAX - 1 else 0
    return zn, s, info


# ---------------------------------------------------------------------------
# Public: full LAMBDA  (LD + reduction + search + back-transform)
# ---------------------------------------------------------------------------
def lambda_py(a: np.ndarray, Q: np.ndarray,
              m: int = 2) -> Tuple[np.ndarray, np.ndarray, int]:
    """Full LAMBDA integer least-squares estimator.

    Equivalent to lambda(n, m, a, Q, F, s) in lambda.c.

    Parameters
    ----------
    a : (n,) float ambiguity vector
    Q : (n,n) covariance matrix (symmetric positive-definite)
    m : number of fixed candidates to return (default 2)

    Returns
    -------
    F    : (n, m) fixed solutions, column 0 = best (lowest residual)
    s    : (m,)  sum-of-squared residuals
           ratio test value = s[1] / s[0]  (higher = more reliable)
    info : 0=ok, -1=LD factorisation failed, -2=search loop overflow
    """
    n = len(a)
    if n <= 0 or m <= 0:
        return np.zeros((n, m)), np.zeros(m), -1

    Q = np.asarray(Q, dtype=float)
    Q = 0.5 * (Q + Q.T)   # enforce symmetry

    L, D, info = _LD(n, Q)
    if info:
        return np.zeros((n, m)), np.zeros(m), info

    Z = np.eye(n, dtype=float)

    # Step 1: LAMBDA reduction  (decorrelate)
    _reduction(n, L, D, Z)

    # Step 2: transform float vector  z = Z' * a
    z = Z.T @ a

    # Step 3: mlambda search in decorrelated space
    E, s, info = _search(n, m, L, D, z)
    if info == -2:
        return np.zeros((n, m)), s, info

    # Step 4: back-transform  F = Z'^{-1} * E = solve(Z', E)
    # Z is integer unimodular, so Z'^{-1} exists with integer entries.
    try:
        F = np.linalg.solve(Z.T, E)
    except np.linalg.LinAlgError:
        return np.zeros((n, m)), s, -1

    return F, s, 0


# ---------------------------------------------------------------------------
# Public: LAMBDA reduction only
# ---------------------------------------------------------------------------
def lambda_reduction_py(Q: np.ndarray) -> Tuple[np.ndarray, int]:
    """LAMBDA decorrelation — returns integer Z-transform matrix.

    Parameters
    ----------
    Q : (n,n) covariance matrix

    Returns
    -------
    Z    : (n,n) integer unimodular transformation matrix
    info : 0=ok, -1=LD failed
    """
    n = Q.shape[0]
    if n <= 0:
        return np.eye(n), -1
    Q = 0.5 * (Q + Q.T)
    L, D, info = _LD(n, Q)
    if info:
        return np.eye(n), info
    Z = np.eye(n, dtype=float)
    _reduction(n, L, D, Z)
    return Z, 0


# ---------------------------------------------------------------------------
# Public: mlambda search only (no reduction)
# ---------------------------------------------------------------------------
def lambda_search_py(a: np.ndarray, Q: np.ndarray,
                     m: int = 2) -> Tuple[np.ndarray, np.ndarray, int]:
    """mlambda search without reduction step.

    Equivalent to lambda_search(n, m, a, Q, F, s) in lambda.c.
    """
    n = len(a)
    if n <= 0 or m <= 0:
        return np.zeros((n, m)), np.zeros(m), -1
    Q = 0.5 * (np.asarray(Q, dtype=float) + np.asarray(Q, dtype=float).T)
    L, D, info = _LD(n, Q)
    if info:
        return np.zeros((n, m)), np.zeros(m), info
    E, s, info = _search(n, m, L, D, a)
    return E, s, info


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import numpy as np

    # 3-ambiguity test with known answer
    a = np.array([175.05, 165.22, 134.31])
    # Diagonal covariance — independent ambiguities
    Q = np.diag([0.0001, 0.0001, 0.0001])

    F, s, info = lambda_py(a, Q, m=2)
    print(f"info={info}")
    print(f"best fix: {F[:, 0]}  (expected [175, 165, 134])")
    print(f"s = {s}")
    print(f"ratio = {s[1]/s[0]:.2f}" if s[0] > 1e-12 else "ratio = inf")

    # 2-ambiguity correlated test
    a2 = np.array([0.3, -0.2])
    Q2 = np.array([[0.001, 0.0005],
                   [0.0005, 0.001]])
    F2, s2, info2 = lambda_py(a2, Q2, m=2)
    print(f"\n2-amb test: fix={F2[:,0]}  ratio={s2[1]/s2[0]:.2f}  info={info2}")
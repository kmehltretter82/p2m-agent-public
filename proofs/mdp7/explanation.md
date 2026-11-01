We prove the structure theorem for the multiperiod consumption-investment problem (Bäuerle–Rieder, Theorem 4.3.3). With $\operatorname{dom} U_c = \operatorname{dom} U_p = [0,\infty)$, no arbitrage, and $\mathbb{E}\|R_n\| < \infty$:

$$V_N = U_p, \qquad V_n(x) = \sup_{(c,a)\in D_n(x)} \Big[ U_c(c) + \mathbb{E}\, V_{n+1}\big((1+i_{n+1})(x-c+a\cdot R_{n+1})\big) \Big],$$

each $V_n$ is strictly increasing, strictly concave and continuous on $[0,\infty)$, and maximizers $f_n^*$ exist and form an optimal policy. The argument uses the no-arbitrage assumption, the integrability of the risks, the independence of $R_1,\dots,R_N$, and the positivity of the bond factors.

**Proof idea.** The whole statement reduces to one step of backward induction. Given a next-period value $w$ that is strictly increasing, strictly concave and continuous, the one-period problem has a continuous value of the same kind and a measurable maximizer. No general measurable selection theorem is needed, because strict concavity makes the maximizer unique once the directions that do nothing are removed.

**Step 1: remove the null directions.** Let $\operatorname{Ker} = \{a : a\cdot R = 0 \text{ almost surely}\}$ and fix a complement $K'$. Directions in $\operatorname{Ker}$ do not change the wealth reached, so it suffices to optimize over $K'$. There, no arbitrage implies that a nonzero $a$ has $\mathbb{P}(a\cdot R < 0) > 0$. Consequently $\psi(a) = \mathbb{E}\max(-a\cdot R, 0)$ is continuous and strictly positive on the unit sphere of $K'$, and homogeneous of degree one.

**Step 2: compactness.** Feasibility requires $x - c + a\cdot R \ge 0$ almost surely, which gives $\psi(a) \le x - c$. So the feasible actions are bounded in proportion to $x$, and they can be parametrized by one fixed compact set $K$ that does not depend on $x$.

**Step 3: the one-period value.** Since $\mathbb{E}\|R\| < \infty$ and a concave $w$ grows at most linearly, dominated convergence shows that the objective $\Phi(x,y)$ is jointly continuous on $[0,\infty)\times K$. Therefore

$$v(x) = \sup_{y\in K} \Phi(x,y)$$

is attained and continuous. Strict monotonicity follows by consuming any extra wealth. Strict concavity follows from strict concavity of $U_c$ when the consumptions differ. When they coincide, the terminal wealths could agree almost surely only if $a\cdot R$ were a nonzero constant almost surely, which no arbitrage excludes.

**Step 4: a measurable maximizer.** By strict concavity the maximizer $y^*(x)$ is unique for $x > 0$. Hence, for a closed set $F$, we have $y^*(x)\in F$ exactly when

$$\sup_{y\in K\cap F} \Phi(x,y) = v(x).$$

Both sides are continuous in $x$, so this set is measurable, and $y^*$ is therefore a measurable function of $x$. Translating back gives a measurable decision rule $g$ with $g(x)\in D_n(x)$.

**Step 5: backward induction.** Starting from $U_p$ and applying the one-step result yields functions $W_n$ and decision rules $f_n$. Independence of $R_{n+1}$ from the later risks factorizes the expected reward of a Markov policy through the first transition. An induction on the remaining horizon then shows that $V_n^{\pi} \le W_n$ for every admissible $\pi$, with equality for $\pi = f$. Thus $V_n = W_n$, which gives the regularity of part (a), the Bellman equation of part (b), and the optimality of $(f_0,\dots,f_{N-1})$ in part (c).

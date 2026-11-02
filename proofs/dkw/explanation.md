We prove the Dvoretzky–Kiefer–Wolfowitz inequality with Massart's constant. For any probability measure $\mu$ on $\mathbb{R}$, with no continuity assumption on its distribution function $F$, any $n$, and any $\varepsilon > 0$,

$$\mu^{\otimes n}\Big(\exists x\in\mathbb{R}:\ \big|\hat F_n(x) - F(x)\big| > \varepsilon\Big) \le 2 e^{-2n\varepsilon^2},$$

where $\hat F_n(x)$ is the fraction of the $n$ samples that are at most $x$. The argument follows the approach of the short proof in arXiv:2403.16651.

**Proof idea.** A single exponential tilt controls one point. To control all points at once, we replace it with a maximal inequality for count statistics along a monotone family of sets, which plays the role of Ville's inequality. An analytic inequality then chooses one tilt parameter that works for every value of the probability simultaneously.

**Step 1: an analytic inequality.** For every $\varepsilon > 0$ there is $\lambda > 0$ such that for all $p > 0$,

$$\log(1+\lambda) + 2\varepsilon^2 \le (p+\varepsilon)\big(\log(p+\lambda) - \log p\big).$$

We parametrize by $t > 0$ with

$$\varepsilon = \tfrac12\big(t\coth t + t - 1\big),$$

which is possible for each $\varepsilon > 0$ by continuity. The right-hand side is minimized over $p$ by a tangency argument, and the claim reduces to the nonnegativity of

$$J(t) = t\coth t - \tfrac12\Big(\frac{t}{\sinh t}\Big)^2 - \tfrac12 - \log\cosh t,$$

which holds because $J(t)\to 0$ as $t\to 0^+$ and the derivative of $J$ has the right sign. As a consequence, if $n(p+\varepsilon) < N$ then

$$(1+\lambda)^n e^{2n\varepsilon^2} p^N \le (p+\lambda)^N.$$

**Step 2: a maximal inequality.** Let $(B_q)_{q\in\mathbb{Q}}$ be a monotone family of measurable sets, let $N_q(x)$ be the number of coordinates of $x$ lying in $B_q$, and let $\nu$ be a finite measure. For $C,\lambda \ge 0$ and any finite set $s$ of rationals,

$$C\,\nu^{\otimes\iota}\Big(\exists q\in s:\ C\,\nu(B_q)^{N_q} \le \big(\nu(B_q)+\lambda\big)^{N_q}\Big) \le \big(\nu(\Omega)+\lambda\big)^{|\iota|}.$$

The proof is by induction on $s$, removing its largest element $a$. We split the sample space according to which coordinates fall in $B_a$. On each cell the coordinates in $B_a$ are distributed by $\nu$ restricted to $B_a$, and the induction hypothesis applies to them, with the event at $a$ itself handled directly. Summing over cells with the binomial theorem gives the bound.

**Step 3: a one-sided bound.** Taking $\nu$ a probability measure and $C = (1+\lambda)^n e^{2n\varepsilon^2}$, Steps 1 and 2 give, after passing from finite sets to all of $\mathbb{Q}$,

$$\nu^{\otimes n}\Big(\exists q\in\mathbb{Q}:\ N_q > n\big(\nu(B_q)+\varepsilon\big)\Big) \le e^{-2n\varepsilon^2}.$$

**Step 4: the two-sided statement.** Since $F$ and $\hat F_n$ are right-continuous, a deviation larger than $\varepsilon$ at a real point $x$ implies one at a rational point. A positive deviation is the event of Step 3 for $B_q = (-\infty, q]$. A negative deviation at $q$ is a positive deviation of the complementary counts, which is the event of Step 3 for the monotone family $B_q = (-q, \infty)$. A union bound over the two cases gives the factor $2$.

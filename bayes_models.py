import logging
import dist_fit
import numpy as np
import scipy.stats as st

# we don't really need to define an interface
class bayes_model:
    log = logging.getLogger(__name__)
    def mse(self, data, *pars, **kwargs):
        """
        the data is assumed to be sequential
        """
        # print(np.array(data))
        return self._mse(np.array(data), *pars, **kwargs)

    def mae(self, data, *pars, **kwargs):
        return self._mae(np.array(data), *pars, **kwargs)
        
    def kld(self, data, *pars, **kwargs):
        """
        data is assumed to be sequential
        kullback-leibler divergence for each point, with updating of bayesian parameters if necessary
        """
        return self._kld(np.array(data), *pars, **kwargs)

## constant delta function, used for test cases
class const_delta_model_gen(bayes_model):
    """
    This model uses a single constant for all predictions.
    Useful as a baseline (often the simple average of all data)
    """
    def _mse(self, data, mu):
        return (mu-data)**2

    def _mae(self, data, const=0):
        return np.abs(const-data)

    def _kld(self, data, mu):
        return np.array([-np.inf if d == mu else np.inf for d in data])
        
delta_const = const_delta_model_gen()

## constant gaussian w/ mean and variance
class const_gauss_model_gen(bayes_model):
    """
    This model uses a single constant for all predictions.
    Useful as a baseline (often the simple average of all data)
    """
    def _mse(self, data, mu, var=0.):
        return (mu-data)**2 + var

    def _mae(self, data, mu, var=0.):
        raise NotImplementedError('technically this is implemented, but it is quite slow')
        # we can loop through manually, at least
        maes = []
        # this takes a looong time and may be inaccurate:
        # we should be able to calculate it manually, in terms of erf
        for d in data:
            maes.append(st.norm.expect(lambda x: np.abs(x-d), loc=mu, scale=np.sqrt(var)))
        return np.array(maes)
    
    def _kld(self, data, mu, var=0.):
        if var == 0:
            log.warning('zero in variance for KL divergence')
            return delta_const.kld(data, mu)
        return (mu-data)**2/(2.0*var) + 0.5*np.log(2.0*np.pi*var)
        
gauss_const = const_gauss_model_gen()



# the model is defined by its posterior predictive distribution, which are compound distributions for easy updating of parameters.
# beta-binomial is a compound of beta with binomial.
# for new observation k, the model is updated:
# alpha -> alpha + k
# beta -> beta + (n-k)
# alpha and beta correspond to those for the beta-binomial (for the posterior predictive),
# or the beta distribution for the prior.
class beta_binomial_model_gen(bayes_model):
    def _mse(self, data, n, alpha0, beta0, lr=1.0):
        # lr can be used to suppress learning
        # we could also apply multiplicative decay after adding, which will result in variance decay even in long careers
        assert((data >= 0).all() and (data <= n).all())
        mses = []
        alpha,beta = alpha0,beta0
        # domain of summation for EV computation:
        support = np.arange(0,n+1)
        for d in data:
            # beta-binomial isn't in scipy - there is an open ticket; the CDF is difficult to implement
            # it's not so bad to compute manually since the domain is finite
            probs = dist_fit.beta_binomial( support, n, alpha, beta )
            mses.append( sum(probs*(support-d)**2) )
            alpha += lr*d
            beta += lr*(n-d)
        # log.debug('alpha, beta = {},{}'.format(alpha,beta))
        return np.array(mses)

    # mean absolute error
    def _mae(self, data, n, alpha0, beta0, lr=1.0):
        assert((data >= 0).all() and (data <= n).all())
        maes = []
        alpha,beta = alpha0,beta0
        support = np.arange(0,n+1)
        for d in data:
            probs = dist_fit.beta_binomial( support, n, alpha, beta )
            maes.append( sum(probs*np.abs(support-d)) )
            alpha += lr*d
            beta += lr*(n-d)
        return np.array(maes)

    # computes Kullback-Leibler divergence
    def _kld(self, data, n, alpha0, beta0, lr=1.0):
        assert((data >= 0).all() and (data <= n).all())
        mses = []
        alpha,beta = alpha0,beta0
        for d in data:
            mses.append( -dist_fit.log_beta_binomial( d, n, alpha, beta ) )
            alpha += lr*d
            beta += lr*(n-d)
        return np.array(mses)

bbinom = beta_binomial_model_gen()


## the negative binomial posterior predictor is a gamma convolved with a poisson
## the update rules are:
# r -> r + lr*k
# beta -> beta + lr where beta = (1-p)/p so:
#   p = 1/(1+beta0+n)
# where if lr < 1 we are slowing the update
# we could also apply multiplicative decay after adding, which will result in variance decay even in long careers
class neg_binomial_model_gen(bayes_model):
    def _mse(self, data, r0, p0, lr=1.0):
        mses = []
        # note these lists should be 1 longer than the data
        rs,ps = self._get_rps(data, r0, p0, lr)
        r_data = data if len(data.shape) == 1 else data[0] / data[1]
        # domain of summation for EV computation - extend out to 5 significance
        for d,r,p in zip(r_data,rs,ps):
            # neg-binomial is in scipy.stats, but uses a different parameterization.
            # will probably be worth it to switch for the function nbinom.expect()
            # the definition on scipy defines p as 1-p compared to wiki.
            # this is confusing... but let's roll with it
            maxct = 5*(p*r)**(1.5)/(1-p)**2 # not clear that computing this initially is the best
            mse = st.nbinom.expect(lambda k: (k-d)**2, args=(r,1-p), maxcount=max(1000,maxct))
            mses.append( mse )
        return np.array(mses)

    def _mae(self, data, *pars):
        raise NotImplementedError

    def _get_rps(self, data, r0, p0, lr=1.0):
        r,b = r0,(1-p0)/p0
        rs = [r]
        bs = [b]
        assert(len(data.shape) <= 2)
        if len(data.shape) == 2:
            for dn,dd in data.T:
                rs.append(rs[-1]+lr*dn)
                bs.append(bs[-1]+lr*dd)
        else:
            for d in data:
                rs.append(rs[-1]+lr*d)
                bs.append(bs[-1]+lr*d)
        ps = 1.0/(1.0+np.array(bs))
        # these probably have one additional element which is not used. it may or may not matter.
        return np.array(rs),np.array(ps)

    # computes Kullback-Leibler divergence
    def _kld(self, data, r0, p0, lr=1.0):
        rs,ps = self._get_rps(data, r0, p0, lr)
        r_data = data if len(data.shape) == 1 else data[0] / data[1]
        # self.log.info('{}, {}, {}'.format(rs,ps,r_data))
        # TODO: this doesn't take non-integer arguments. guess we need our own.
        # TODO: or we can find a scaling property to use and plug in integer?
        result = -st.nbinom.logpmf(r_data, rs[:-1], 1-ps[:-1])
        # result = -dist_fit.log_neg_binom(r_data, rs[:-1], 1-ps[:-1])
        if np.isinf(result).any():
            self.log.error('inf in KLD')
            self.log.error('rs = {}, ps = {}'.format(rs, ps))
            self.log.error('data = {}\n result = {}'.format(r_data, result))
        return result
        # klds = []
        # for d in data:
        #     # mses.append( -dist_fit.log_neg_binomial( d, r, p ) )
        #     p = 1/(1+b)
        #     # recall that scipy uses a strange (backwards?) definition for the p value
        #     kld = -st.nbinom.logpdf(d, r, 1-p)
        #     logging.info('{} ?= {}'.format(-dist_fit.log_neg_binomial( d, r, p ), kld))
        #     klds.append(kld)
        #     r += lr*d
        #     b += lr
        # return np.array(klds)

nbinom = neg_binomial_model_gen()



## TODO: work this function/model into the class structure above.
## the "mean" model can perhaps be made bayesian, using unknown variance -- student's t?

# mean-squared-error of model that uses mean of past
def mse_model_mean(data, default=0):
    mses = []
    if data.size == 0:
        return default
    for i_d in range(data.size):
        # mean_so_far = data.iloc[:i_d].mean() if i_d > 0 else default # this indexer works for Series input
        # mses.append( (mean_so_far-data.iloc[i_d])**2 )
        mean_so_far = data[:i_d].mean() if i_d > 0 else default
        mses.append( (mean_so_far-data[i_d])**2 )
    return np.array(mses)



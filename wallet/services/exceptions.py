class WalletError(Exception):
    """Base exception for all wallet service errors."""


class InsufficientFundsError(WalletError):
    """Raised when a debit is attempted for more than the wallet's available balance."""


class WalletInactiveError(WalletError):
    """Raised when an operation is attempted on a deactivated wallet."""


class VirtualAccountCreationError(Exception):
    """Raised when Zainpay virtual account creation fails."""


class ZainpayTransferError(Exception):
    """Raised when a Zainpay funds transfer fails or cannot be confirmed."""


class ZainpayCheckoutError(Exception):
    """Raised when starting a Zainpay checkout session fails."""

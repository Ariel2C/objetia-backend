from enum import Enum
from typing import Optional, List
from datetime import datetime
from sqlmodel import Field, Relationship
from src.config.database import Base

class TransactionType(str, Enum):
    CREDIT_SALE = "sale_revenue"    # Ingreso de dinero por vender un artículo
    DEBIT_WITHDRAW = "withdrawal"   # Retiro de dinero hacia cuenta bancaria
    INTERNAL_PURCHASE = "purchase"  # Uso del saldo interno para comprar en la web

class TransactionStatus(str, Enum):
    FROZEN = "frozen"               # En garantía (Periodo de retención activo)
    AVAILABLE = "available"         # Liberado, listo para gastar o retirar
    COMPLETED = "completed"         # Retiro bancario aprobado con éxito


# ==============================================================================
# TABLA: BILLETERA VIRTUAL (Balance de cada usuario)
# ==============================================================================
class Wallet(Base, table=True):
    __tablename__ = "wallets"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    user_id: int = Field(foreign_key="users.id", unique=True, nullable=False, index=True)
    
    # Separación contable estricta
    balance_available: float = Field(default=0.0, nullable=False) # Listo para retirar/gastar
    balance_frozen: float = Field(default=0.0, nullable=False)    # En garantía (Escrow)
    
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


# ==============================================================================
# TABLA: HISTORIAL DE TRANSACCIONES (Libro Mayor Contable Inmutable)
# ==============================================================================
class WalletTransaction(Base, table=True):
    __tablename__ = "wallet_transactions"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    wallet_id: int = Field(foreign_key="wallets.id", nullable=False, index=True)
    
    # Datos de la Operación
    amount: float = Field(nullable=False)         # Monto neto final que recibe el usuario
    marketplace_commission: float = Field(default=0.0, nullable=False) # Comisión que ganó la web
    
    type: TransactionType = Field(nullable=False)
    status: TransactionStatus = Field(default=TransactionStatus.FROZEN, index=True)
    
    # Datos de liberación temporal
    available_at: datetime = Field(nullable=False, index=True) # Cuándo debe pasar a disponible
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    
    # Destino del retiro (CBU / CVU / Alias) si aplica
    destination_account: Optional[str] = Field(default=None, nullable=True)


# ==============================================================================
# TABLA: CUENTAS BANCARIAS DE RETIRO (CBU/CVU/Alias guardados por usuario)
# ==============================================================================
class WalletPayoutAccount(Base, table=True):
    __tablename__ = "wallet_payout_accounts"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    user_id: int = Field(foreign_key="users.id", nullable=False, index=True)
    cbu_cvu: str = Field(nullable=False, max_length=50, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    last_used_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


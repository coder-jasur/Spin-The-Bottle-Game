from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from src.app.database.models.wallet import Wallet
from src.app.database.models.transaction import Transaction

class WalletRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_wallet(self, user_id: int) -> Wallet | None:
        stmt = select(Wallet).where(Wallet.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_wallet(self, user_id: int, initial_balance: int = 0) -> Wallet:
        wallet = Wallet(user_id=user_id)
        self.session.add(wallet)
        await self.session.commit()
        return wallet

    async def add_currency(self, user_id: int, amount: int, currency: str, transaction_type: str, description: str = None):
        """Istalgan valyutani (gift_tokens, stars_coin, hearts) qo'shish"""
        values = {}
        if currency == "gift_tokens":
            values = {Wallet.gift_tokens: Wallet.gift_tokens + amount}
        elif currency == "stars_coin":
            values = {Wallet.stars_coin: Wallet.stars_coin + amount}
        elif currency == "hearts":
            values = {Wallet.hearts: Wallet.hearts + amount}
        
        if values:
            stmt = update(Wallet).where(Wallet.user_id == user_id).values(values)
            await self.session.execute(stmt)
        
        # Tranzaksiyani saqlash
        transaction = Transaction(
            user_id=user_id,
            amount=amount,
            currency=currency,
            type=transaction_type,
            description=description
        )
        self.session.add(transaction)
        await self.session.commit()

    async def spend_currency(self, user_id: int, amount: int, currency: str, transaction_type: str, description: str = None) -> bool:
        """Istalgan valyutani sarflash"""
        wallet = await self.get_wallet(user_id)
        if not wallet: return False
        
        current_balance = 0
        target_column = None
        if currency == "gift_tokens":
            current_balance = wallet.gift_tokens
            target_column = Wallet.gift_tokens
        elif currency == "stars_coin":
            current_balance = wallet.stars_coin
            target_column = Wallet.stars_coin
        elif currency == "hearts":
            current_balance = wallet.hearts
            target_column = Wallet.hearts
            
        if current_balance < amount:
            return False
        
        # Balansni kamaytirish
        stmt = update(Wallet).where(Wallet.user_id == user_id).values({target_column: target_column - amount})
        await self.session.execute(stmt)
        
        # Tranzaksiya
        transaction = Transaction(
            user_id=user_id,
            amount=-amount,
            currency=currency,
            type=transaction_type,
            description=description
        )
        self.session.add(transaction)
        await self.session.commit()
        return True

from datetime import datetime

def initialize_position(position, stop_loss, take_profit):
    """
    Creates a professionally managed position.
    """

    position["stop_loss"] = stop_loss
    position["take_profit"] = take_profit

    position["highest_price"] = position["entry_price"]
    position["lowest_price"] = position["entry_price"]

    position["breakeven"] = False
    position["partial_taken"] = False

    position["opened_at"] = datetime.now().isoformat()

    return position


def update_position(position, current_price):
    """
    Updates highest and lowest price reached.
    """

    if current_price > position["highest_price"]:
        position["highest_price"] = current_price

    if current_price < position["lowest_price"]:
        position["lowest_price"] = current_price

    return position

def check_position_exit(position, current_price):
    """
    Determines whether a position should be closed.
    """

    if current_price <= position["stop_loss"]:
        return True, "Stop Loss"

    if current_price >= position["take_profit"]:
        return True, "Take Profit"

    return False, ""
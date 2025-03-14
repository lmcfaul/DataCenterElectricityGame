import random
import string
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
from flask_admin import Admin, BaseView, expose
from flask_admin.contrib.sqla import ModelView
import os
import zipfile
from io import BytesIO
from flask import send_file
import pandas as pd

side_deal_fee_during_year_3 = 50


app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///data_center_experiment.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")  # Enable real-time communication

def generate_offer_id():
    characters = string.ascii_letters + string.digits  # A-Z, a-z, 0-9
    return ''.join(random.choices(characters, k=15))

# Define the database models
class Bid(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(50), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    season = db.Column(db.String(50), nullable=False)
    period = db.Column(db.Integer, nullable=False)
    plant = db.Column(db.String(50), nullable=False)
    marginal_cost = db.Column(db.Float, nullable=False)
    bid = db.Column(db.Float, nullable=False)
    run_bool = db.Column(db.Boolean, nullable=False, default=False)

class GlobalState(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    current_year = db.Column(db.Integer, default=1)
    current_season = db.Column(db.String(50), default="Summer")

class Globalprices(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    electricity_price = db.Column(db.Float, default=0.0)
    electricity_price_with_fee = db.Column(db.Float, default=0.0)
    total_demand = db.Column(db.Float,default=9200)
    total_fee = db.Column(db.Float,default=368000)
    fee_per_MW = db.Column(db.Float,default=40)
    fee_per_side_deal_MW = db.Column(db.Float,default=0)


class ConnectedUser(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(50), nullable=False)
    profits = db.Column(db.Float, nullable=False, default=0.0)

class Prices(db.Model):  # New Prices class
    id = db.Column(db.Integer, primary_key=True)
    season = db.Column(db.String(50), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    period = db.Column(db.Integer, nullable=False)
    MWs = db.Column(db.Float, nullable=False)
    price = db.Column(db.Float, nullable=False)

class OfferedDeals(db.Model):
    id = db.Column(db.String(15), primary_key=True, default=generate_offer_id)
    user_id_datacenter = db.Column(db.String(50), nullable=False)
    user_id_producer = db.Column(db.String(50), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    message = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(50), nullable=False, default="pending")

admin = Admin(app, name='Admin', template_mode='bootstrap3')
admin.add_view(ModelView(ConnectedUser, db.session))
admin.add_view(ModelView(Prices, db.session))
admin.add_view(ModelView(GlobalState, db.session))
admin.add_view(ModelView(Bid, db.session))
admin.add_view(ModelView(OfferedDeals, db.session))
admin.add_view(ModelView(Globalprices, db.session))

class CustomAdminView(BaseView):
    @expose('/')
    def index(self):
        return self.render('admin/custom_admin_view.html')

    @expose('/calculate_equilibrium_and_profits', methods=['POST'])
    def calculate_equilibrium_and_profits_view(self):
        calculate_equilibrium_and_profits()
        return self.render('admin/custom_admin_view.html', message="Equilibrium and profits calculated successfully.")

    @expose('/update_year_and_season', methods=['POST'])
    def update_year_and_season_view(self):
        update_year_and_season()
        return self.render('admin/custom_admin_view.html', message="Year and season updated successfully.")

# Add the custom admin view to the admin interface
admin.add_view(CustomAdminView(name='Custom Admin'))

# Initialize the database
with app.app_context():
    db.create_all()
    if not GlobalState.query.first():
        db.session.add(GlobalState())
        db.session.commit()

# Global variables to store profits and equilibrium prices
profits = {}
equilibrium_prices = {}

# Dictionary to store user rooms
user_rooms = {}

@app.route('/')
def index():
    return render_template('index.html')  # Renders the landing page

@app.route('/producer')
def producer():
    name = request.args.get('name')
    if not ConnectedUser.query.filter_by(user_id=name, role='producer').first():
        db.session.add(ConnectedUser(user_id=name, role='producer'))
        db.session.commit()

    global_state = GlobalState.query.first()
    prices = Prices.query.all()
    
    return render_template('producer.html', name=name)  # Renders the producer page

@app.route('/data_center')
def data_center():
    name = request.args.get('name')
    if not ConnectedUser.query.filter_by(user_id=name, role='data_center').first():
        db.session.add(ConnectedUser(user_id=name, role='data_center'))
        db.session.commit()
    return render_template('data_center.html', name=name)  # Renders the data center page

@app.route('/server')
def server_dashboard():
    global_state = GlobalState.query.first()
    return render_template('server.html', current_year=global_state.current_year, current_season=global_state.current_season)  # Renders the server dashboard

@app.route('/update_year_and_season')
def update_year_and_season_route():
    update_year_and_season()
    calculate_equilibrium_and_profits()
    return "Year and season updated, and equilibrium and profits calculated"

@app.route('/get_database')
def get_database():
    bids = Bid.query.all()
    connected_users = ConnectedUser.query.all()
    global_state = GlobalState.query.first()
    prices = Prices.query.all()
    offered_deals = OfferedDeals.query.all()
    global_prices = Globalprices.query.first()

    if not global_prices:
        global_prices = Globalprices()
        db.session.add(global_prices)
        db.session.commit()    

    bid_data = [
        {
            'user_id': bid.user_id,
            'year': bid.year,
            'season': bid.season,
            'period': bid.period,
            'plant': bid.plant,
            'marginal_cost': bid.marginal_cost,
            'bid': bid.bid,
            'run_bool': bid.run_bool
        } for bid in bids
    ]

    user_data = [
        {
            'user_id': user.user_id,
            'role': user.role,
            'profits': user.profits
        } for user in connected_users
    ]

    global_state_data = {
        'current_year': global_state.current_year,
        'current_season': global_state.current_season
    }

    prices_data = [
        {
            'year': price.year,
            'season': price.season,
            'period': price.period,
            'MWs': price.MWs,
            'price': price.price
        } for price in prices
    ]

    producers_data = [
        {
            'user_id': user.user_id,
            'profits': user.profits
        } for user in connected_users if user.role == 'producer'
    ]
    
    data_center_costs = [
        {
            'user_id': user.user_id,
            'electricity_cost': -user.profits
        } for user in connected_users if user.role == 'data_center'
    ]

    offers_data = [
        {
            'id': offer.id,
            'user_id_datacenter': offer.user_id_datacenter,
            'user_id_producer': offer.user_id_producer,
            'year': offer.year,
            'price': offer.price,
            'message': offer.message,
            'status': offer.status
        } for offer in offered_deals
    ]

    data_center_plans = []
    for user in connected_users:
        if user.role == 'data_center':
            accepted_offers = OfferedDeals.query.filter_by(user_id_datacenter=user.user_id, year=global_state.current_year, status="accepted").all()
            if len(accepted_offers) == 0:
                plan = "electricity auction market"
                price1 = 0
                price2 = 0
                deals = 0
            elif len(accepted_offers) == 1:
                plan = "colocation agreement"
                price1 = accepted_offers[0].price
                price2 = 0
                deals = 1

            elif len(accepted_offers) == 2:
                plan = "colocation agreement"
                price1 = accepted_offers[0].price
                price2 = accepted_offers[1].price
                deals = 2
            else:
                plan = "colocation agreement"
                price1 = 0
                price2 = 0
                deals = 0
            data_center_plans.append({
                'user_id': user.user_id,
                'plan': plan,
                'price1': price1,
                'price2': price2,
                'deals': deals
            })
    


    global_prices_data = [
        {
            'electricity_price': global_prices.electricity_price,
            'electricity_price_with_fee': global_prices.electricity_price_with_fee,
            'total_demand': global_prices.total_demand,
            'total_fee': global_prices.total_fee,
            'fee_per_MW': global_prices.fee_per_MW,
            'fee_per_side_deal_MW': global_prices.fee_per_side_deal_MW
        } if global_prices else {}
    ]

    return jsonify({
        'bids': bid_data,
        'users': user_data,
        'global_state': global_state_data,
        'prices': prices_data,
        'producers': producers_data,
        'offers': offers_data,
        'data_center_costs': data_center_costs,
        'data_center_plans': data_center_plans,
        'global_prices': global_prices_data[0]
    })

@app.route('/get_equilibrium_and_profits')
def get_equilibrium_and_profits():
    return jsonify({'equilibrium_prices': equilibrium_prices, 'profits': profits})

@app.route('/restart', methods=['POST'])
def restart():
    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(GlobalState())
        db.session.commit()
    return redirect(url_for('customadminview.index'))  # Redirect back to the custom admin view

@app.route('/submit_bids', methods=['POST'])
def submit_bids():
    data = request.json
    bids = data['bids']
    df = pd.DataFrame(bids)
    for _, row in df.iterrows():
        bid = Bid(
            user_id=row['user_id'],
            year=row['year'],
            season=row['season'],
            period=row['period'],
            plant=row['plant'],
            marginal_cost=row['marginal_cost'],
            bid=row['bid'],
            run_bool=False  # Set run_bool to False when submitting bids
        )
        db.session.add(bid)
    db.session.commit()
    return jsonify({"status": "success", "message": "Bids submitted successfully"})


@app.route('/revoke_offer', methods=['POST'])
def revoke_offer():
    offer_id = request.json['offer_id']
    offer = OfferedDeals.query.get(offer_id)
    if offer and offer.status == "pending":
        offer.status = "revoked"
        db.session.commit()
        return jsonify({"status": "success", "message": "Offer revoked successfully"})
    return jsonify({"status": "error", "message": "Offer not found or already processed"})


@app.route('/accept_offer', methods=['POST'])
def accept_offer():
    offer_id = request.json['offer_id']
    offer = OfferedDeals.query.get(offer_id)
    if offer and offer.status == "pending":
        # Accept the offer
        offer.status = "accepted"
        db.session.commit()

        # Get the current year from GlobalState
        current_year = GlobalState.query.first().current_year

        # Check the number of accepted offers for the data center in the current year
        accepted_offers = OfferedDeals.query.filter_by(user_id_datacenter=offer.user_id_datacenter, year=current_year, status="accepted").count()
        if accepted_offers >= 2:
            # Revoke all other pending offers for the data center in the current year
            pending_offers = OfferedDeals.query.filter_by(user_id_datacenter=offer.user_id_datacenter, year=current_year, status="pending").all()
            for pending_offer in pending_offers:
                pending_offer.status = "revoked"
            db.session.commit()

        return jsonify({"status": "success", "message": "Offer accepted successfully"})
    return jsonify({"status": "error", "message": "Offer not found or already processed"})




@app.route('/decline_offer', methods=['POST'])
def decline_offer():
    offer_id = request.json['offer_id']
    offer = OfferedDeals.query.get(offer_id)
    if offer and offer.status == "pending":
        offer.status = "declined"
        db.session.commit()
        return jsonify({"status": "success", "message": "Offer declined successfully"})
    return jsonify({"status": "error", "message": "Offer not found or already processed"})



@app.route('/submit_offer', methods=['POST'])
def submit_offer():
    user_id_datacenter = request.form['user_id_datacenter']
    user_id_producer = request.form['user_id_producer']
    year = request.form['year']
    price = request.form['price']
    message = request.form['message']
    
    # Get the current year from GlobalState
    current_year = GlobalState.query.first().current_year

    # Check the number of accepted offers for the data center in the current year
    accepted_offers = OfferedDeals.query.filter_by(user_id_datacenter=user_id_datacenter, year=current_year, status="accepted").count()
    if accepted_offers >= 2:
        return jsonify({"status": "error", "message": "Data center already has two accepted offers for the current year"})


    if current_year == 1:
        return jsonify({"status": "error", "message": "Data center can't make offers in year 1"})
    

    offer = OfferedDeals(
        id=generate_offer_id(),
        user_id_datacenter=user_id_datacenter,
        user_id_producer=user_id_producer,
        year=year,
        price=price,
        message=message,
        status="pending"
    )
    db.session.add(offer)
    db.session.commit()
    
    return redirect(url_for('data_center', name=user_id_datacenter))

@app.route('/get_producers')
def get_producers():
    producers = ConnectedUser.query.filter_by(role='producer').all()
    producers_data = [{'user_id': producer.user_id} for producer in producers]
    return jsonify(producers_data)

@socketio.on('submit_bid')
def handle_bid(data):
    bids = data['bids']
    df = pd.DataFrame(bids)
    for _, row in df.iterrows():
        bid = Bid(
            user_id=row['user_id'],
            year=row['year'],
            season=row['season'],
            period=row['period'],
            plant=row['plant'],
            marginal_cost=row['marginal_cost'],
            bid=row['bid'],
            run_bool=False  # Set run_bool to False when submitting bids
        )
        db.session.add(bid)
    db.session.commit()

    # Send updated bid list to all clients
    emit_bids()

@socketio.on('request_bids')
def handle_request_bids():
    emit_bids()

@socketio.on('request_connected_users')
def handle_request_connected_users():
    producers = [user.user_id for user in ConnectedUser.query.filter_by(role='producer').all()]
    data_centers = [user.user_id for user in ConnectedUser.query.filter_by(role='data_center').all()]
    emit('update_connected_users', {'producers': producers, 'data_centers': data_centers})

@socketio.on('update_year_and_season')
def handle_update_year_and_season():
    print("Received update_year_and_season event")
    update_year_and_season()


@app.route('/download_csvs')
def download_csvs():
    # Create a directory to store the CSV files
    csv_dir = 'csv_files'
    if not os.path.exists(csv_dir):
        os.makedirs(csv_dir)

    # Generate CSV files from the database
    generate_csv_files(csv_dir)

    # Create a ZIP file in memory
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for root, _, files in os.walk(csv_dir):
            for file in files:
                file_path = os.path.join(root, file)
                zip_file.write(file_path, os.path.relpath(file_path, csv_dir))

    # Clean up the CSV files
    for file in os.listdir(csv_dir):
        os.remove(os.path.join(csv_dir, file))
    os.rmdir(csv_dir)

    # Send the ZIP file as a response
    zip_buffer.seek(0)
    return send_file(zip_buffer, mimetype='application/zip', as_attachment=True, download_name='csv_files.zip')

def generate_csv_files(csv_dir):
    # Exporting Bid table to CSV
    bids = Bid.query.all()
    bids_df = pd.DataFrame([{
        'id': bid.id,
        'user_id': bid.user_id,
        'year': bid.year,
        'season': bid.season,
        'period': bid.period,
        'plant': bid.plant,
        'marginal_cost': bid.marginal_cost,
        'bid': bid.bid,
        'run_bool': bid.run_bool
    } for bid in bids])
    bids_df.to_csv(os.path.join(csv_dir, 'bids.csv'), index=False)

    # Exporting GlobalState table to CSV
    global_states = GlobalState.query.all()
    global_states_df = pd.DataFrame([{
        'id': state.id,
        'current_year': state.current_year,
        'current_season': state.current_season
    } for state in global_states])
    global_states_df.to_csv(os.path.join(csv_dir, 'global_states.csv'), index=False)

    # Exporting Globalprices table to CSV
    global_prices = Globalprices.query.all()
    global_prices_df = pd.DataFrame([{
        'id': price.id,
        'electricity_price': price.electricity_price,
        'electricity_price_with_fee': price.electricity_price_with_fee,
        'total_demand': price.total_demand,
        'total_fee': price.total_fee,
        'fee_per_MW': price.fee_per_MW,
        'fee_per_side_deal_MW': price.fee_per_side_deal_MW
    } for price in global_prices])
    global_prices_df.to_csv(os.path.join(csv_dir, 'global_prices.csv'), index=False)

    # Exporting ConnectedUser table to CSV
    connected_users = ConnectedUser.query.all()
    connected_users_df = pd.DataFrame([{
        'id': user.id,
        'user_id': user.user_id,
        'role': user.role,
        'profits': user.profits
    } for user in connected_users])
    connected_users_df.to_csv(os.path.join(csv_dir, 'connected_users.csv'), index=False)

    # Exporting Prices table to CSV
    prices = Prices.query.all()
    prices_df = pd.DataFrame([{
        'id': price.id,
        'season': price.season,
        'year': price.year,
        'period': price.period,
        'MWs': price.MWs,
        'price': price.price
    } for price in prices])
    prices_df.to_csv(os.path.join(csv_dir, 'prices.csv'), index=False)

    # Exporting OfferedDeals table to CSV
    offered_deals = OfferedDeals.query.all()
    offered_deals_df = pd.DataFrame([{
        'id': deal.id,
        'user_id_datacenter': deal.user_id_datacenter,
        'user_id_producer': deal.user_id_producer,
        'year': deal.year,
        'price': deal.price,
        'message': deal.message,
        'status': deal.status
    } for deal in offered_deals])
    offered_deals_df.to_csv(os.path.join(csv_dir, 'offered_deals.csv'), index=False)



def update_year_and_season():
    global_state = GlobalState.query.first()
    global_prices = Globalprices.query.first()
    seasons = ["Summer", "Winter"]
    current_season_index = seasons.index(global_state.current_season)
    if current_season_index == 1:
        global_state.current_season = seasons[0]
        global_state.current_year += 1
    else:
        global_state.current_season = seasons[current_season_index + 1]
    if global_state.current_year == 3:
        global_prices.fee_per_side_deal_MW = side_deal_fee_during_year_3
    db.session.commit()
    

def calculate_equilibrium_and_profits():
    global profits, equilibrium_prices
    profits = {}
    equilibrium_prices = {}
    current_year = GlobalState.query.first().current_year
    current_season = GlobalState.query.first().current_season
    # find the total amount of accepted offers in the current year
    accepted_offers_count = OfferedDeals.query.filter_by(status="accepted", year=current_year).count()
    periods = {
        1: (800 - (100 * accepted_offers_count)),
        2: (1200 - (100 * accepted_offers_count)),
        3: (1600 - (100 * accepted_offers_count)),
        4: (1000 - (100 * accepted_offers_count))
    }

    global_state = GlobalState.query.first() # Get the current year and season

    for period, demand in periods.items():
        bids = Bid.query.filter_by(period=period, year=current_year, season=current_season).order_by(Bid.bid).all()
        total_supply = 0
        clearing_price = 0

        for bid in bids:
            total_supply += 100
            #change the run_bool to True for this bid
            bid.run_bool = True
            db.session.add(bid)
            db.session.commit()
            if total_supply >= demand:
                clearing_price = bid.bid
                break

        equilibrium_prices[period] = clearing_price

        # add the clearing price to the prices table
        price_entry = Prices.query.filter_by(year=global_state.current_year, season=global_state.current_season, period=period).first()
        if price_entry:
            price_entry.price = clearing_price
        else:
    # Determine the id value
            if not Prices.query.first():
                new_id = 1
            else:
                new_id = Prices.query.order_by(Prices.id.desc()).first().id + 1

            # Create the Prices instance with the determined id
            price_entry = Prices(
                id=new_id,
                season=global_state.current_season,
                year=global_state.current_year,
                period=period,
                MWs=total_supply,
                price=clearing_price
            )
            db.session.add(price_entry)



        for bid in bids:
            user = ConnectedUser.query.filter_by(user_id=bid.user_id).first()
            if bid.run_bool:
                profit = (clearing_price - bid.marginal_cost) * 100
            else:
                profit = 0
            if user:
                user.profits += profit
                db.session.add(user)

    db.session.commit()

    # calculate profits from side deals

    # filter all accepted offers in the current year
    accepted_offers = OfferedDeals.query.filter_by(status="accepted", year=current_year).all()
    for offer in accepted_offers:
        producer = ConnectedUser.query.filter_by(user_id=offer.user_id_producer).first()
        data_center = ConnectedUser.query.filter_by(user_id=offer.user_id_datacenter).first()
        if producer and data_center:
            producer.profits += offer.price * 400
            db.session.add(producer)
    db.session.commit()

    #go through each producer and count the accepted offers
    producers = ConnectedUser.query.filter_by(role='producer').all()
    for producer in producers:
        accepted_offers = OfferedDeals.query.filter_by(user_id_producer=producer.user_id, year=current_year, status="accepted").count()
        production_cost = [0,10,17.5,26.67,42.5,52]
        producer.profits -= production_cost[accepted_offers] * (100*accepted_offers)
        db.session.add(producer)
        db.session.commit()



    global_state = GlobalState.query.first()
    current_year = global_state.current_year

    # Calculate the average electricity price for the current year
    prices = Prices.query.filter_by(year=current_year).all()
    if prices:
        avg_electricity_price = sum(price.price for price in prices) / len(prices)
    else:
        avg_electricity_price = 0.0

    # Calculate the total demand
    accepted_offers_count = OfferedDeals.query.filter_by(year=current_year, status="accepted").count()
    total_demand = 4600 - (400 * accepted_offers_count)

    # Calculate the fee per MW
    total_fee = 184000
    if total_demand > 0:
        fee_per_MW = total_fee / total_demand
    else:
        fee_per_MW = 0.0

    # Update the Globalprices table
    global_prices = Globalprices.query.first()
    if not global_prices:
        global_prices = Globalprices()
        db.session.add(global_prices)

    global_prices.electricity_price = avg_electricity_price
    global_prices.electricity_price_with_fee = avg_electricity_price + fee_per_MW
    global_prices.total_demand = total_demand
    global_prices.total_fee = total_fee
    global_prices.fee_per_MW = fee_per_MW

    db.session.commit()

    #calculate electricity costs from data centers
    data_centers = ConnectedUser.query.filter_by(role='data_center').all()
    for data_center in data_centers:
        accepted_offers = OfferedDeals.query.filter_by(user_id_datacenter=data_center.user_id, year=current_year, status="accepted").all()
        if accepted_offers:
            electricity_cost = sum((offer.price + global_prices.fee_per_side_deal_MW) * 100 for offer in accepted_offers)
            for offer in accepted_offers:
                if electricity_cost / (offer.price + global_prices.fee_per_side_deal_MW) ==100:
                    electricity_cost += global_prices.electricity_price_with_fee * 100
        else:
            electricity_cost = global_prices.electricity_price_with_fee * 200
        data_center.profits -= electricity_cost  # Subtract the electricity cost from profits
        db.session.add(data_center)
        db.session.commit()








def emit_bids():
    bids = Bid.query.all()
    bid_data = {}
    for bid in bids:
        if bid.user_id not in bid_data:
            bid_data[bid.user_id] = {}
        if bid.plant not in bid_data[bid.user_id]:
            bid_data[bid.user_id][bid.plant] = {}
        bid_data[bid.user_id][bid.plant][bid.period] = bid.bid
    socketio.emit('update_bids', bid_data)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5001, debug=True)
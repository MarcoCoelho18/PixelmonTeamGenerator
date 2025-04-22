from flask import Flask, render_template, request
import requests
import random
import pickle
import os
import atexit
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)

# Cache setup
CACHE_FILE = 'pokemon_cache.pkl'
pokemon_cache = {}
evolution_families = {}

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'rb') as f:
            data = pickle.load(f)
            return data['pokemon'], data['evolution']
    return {}, {}

def save_cache():
    with open(CACHE_FILE, 'wb') as f:
        pickle.dump({'pokemon': pokemon_cache, 'evolution': evolution_families}, f)

pokemon_cache, evolution_families = load_cache()
atexit.register(save_cache)

def clean_pixelmon_name(name):
    """Simple URL formatting - just replace spaces with underscores"""
    return name.replace(' ', '_')

def get_evolution_family(species_url):
    """Get all Pokémon in the same evolution family"""
    if species_url in evolution_families:
        return evolution_families[species_url]
    
    try:
        response = requests.get(species_url)
        species_data = response.json()
        
        if not species_data['evolution_chain']:
            evolution_families[species_url] = [species_data['id']]
            return [species_data['id']]
        
        evolution_response = requests.get(species_data['evolution_chain']['url'])
        evolution_data = evolution_response.json()
        
        family_ids = []
        chain = evolution_data['chain']
        
        def get_chain_ids(chain):
            species_id = int(chain['species']['url'].split('/')[-2])
            family_ids.append(species_id)
            for evolution in chain['evolves_to']:
                get_chain_ids(evolution)
        
        get_chain_ids(chain)
        evolution_families[species_url] = family_ids
        return family_ids
    
    except Exception as e:
        print(f"Error fetching evolution chain: {e}")
        return []

def get_pokemon_data(pokemon_id):
    """Fetch Pokémon data from PokeAPI or cache"""
    if pokemon_id in pokemon_cache:
        return pokemon_cache[pokemon_id]
    
    try:
        response = requests.get(f"https://pokeapi.co/api/v2/pokemon/{pokemon_id}")
        if response.status_code == 200:
            data = response.json()
            species_response = requests.get(data['species']['url'])
            species_data = species_response.json()
            
            # Get English species name
            species_name = next(
                (name['name'] for name in species_data['names'] 
                 if name['language']['name'] == 'en'),
                species_data['name']
            )
            
            # Get evolution family
            evolution_family = get_evolution_family(data['species']['url'])
            
            # Get first evolution in chain
            first_evolution = None
            if species_data['evolution_chain']:
                evolution_response = requests.get(species_data['evolution_chain']['url'])
                evolution_data = evolution_response.json()
                chain = evolution_data['chain']
                first_evo_id = int(chain['species']['url'].split('/')[-2])
                if first_evo_id != pokemon_id:
                    first_evolution = get_pokemon_data(first_evo_id)
            
            # Create Pixelmon wiki URL
            pixelmon_name = clean_pixelmon_name(species_name)
            pixelmon_url = f"https://pixelmonmod.com/wiki/{pixelmon_name}"
            
            pokemon = {
                'id': data['id'],
                'name': species_name,
                'sprite': data['sprites']['front_default'],
                'types': [t['type']['name'].capitalize() for t in data['types']],
                'is_legendary': species_data['is_legendary'] or species_data['is_mythical'],
                'first_evolution': first_evolution,
                'wiki_url': pixelmon_url,
                'evolution_family': evolution_family
            }
            
            pokemon_cache[pokemon_id] = pokemon
            return pokemon
    except Exception as e:
        print(f"Error fetching Pokémon {pokemon_id}: {e}")
    return None

def pre_cache_pokemon():
    """Cache all Pokémon data at startup"""
    print("Pre-caching Pokémon data...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        executor.map(get_pokemon_data, range(1, 1026))  # Adjust range as needed
    print("Pre-caching complete!")

def get_random_team(legendary_count):
    """Generate a random team with specified number of legendaries"""
    team = []
    used_families = set()
    
    # Get all available Pokémon from cache
    available_pokemon = [p for p in pokemon_cache.values() if p is not None]
    
    # Separate legendaries and non-legendaries
    legendaries = [p for p in available_pokemon if p['is_legendary']]
    regulars = [p for p in available_pokemon if not p['is_legendary']]
    
    # Shuffle both lists for randomness
    random.shuffle(legendaries)
    random.shuffle(regulars)
    
    # Add required legendaries
    for legendary in legendaries:
        if len(team) >= legendary_count:
            break
        family_conflict = any(fam in used_families for fam in legendary['evolution_family'])
        if not family_conflict:
            team.append(legendary)
            used_families.update(legendary['evolution_family'])
    
    # Add remaining Pokémon
    for pokemon in regulars:
        if len(team) >= 6:
            break
        family_conflict = any(fam in used_families for fam in pokemon['evolution_family'])
        if not family_conflict:
            team.append(pokemon)
            used_families.update(pokemon['evolution_family'])
    
    # Final shuffle
    random.shuffle(team)
    return team[:6]

@app.route('/', methods=['GET', 'POST'])
def index():
    legendary_count = int(request.form.get('legendary_count', 0)) if request.method == 'POST' else 0
    team = get_random_team(legendary_count) if request.method in ['GET', 'POST'] else None
    return render_template('index.html', team=team, legendary_count=legendary_count)

if __name__ == '__main__':
    if not pokemon_cache:
        pre_cache_pokemon()
    app.run(debug=True)
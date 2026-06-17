import csv
import json
import math
import re
from collections import defaultdict 

def get_angle(p1, p2, p3):
    dx1, dy1 = p2['lng'] - p1['lng'], p2['lat'] - p1['lat']
    dx2, dy2 = p3['lng'] - p2['lng'], p3['lat'] - p2['lat']
    angle = math.degrees(math.atan2(dx2, dy2) - math.atan2(dx1, dy1))
    return abs(angle)

def calc_distance(p1, p2):
    R = 6371000
    phi1, phi2 = math.radians(p1['lat']), math.radians(p2['lat'])
    dphi = math.radians(p2['lat'] - p1['lat'])
    dlambda = math.radians(p2['lng'] - p1['lng'])
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return round(R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a))), 2)

# =========================================================
# STEP 1: ดึงข้อมูลพนักงานและรถยนต์จาก CSV (Master Data)
# =========================================================
workers_map = {}
vehicles_map = {}
worker_counter = 1

with open("bma_waste_routes_v2.csv", mode='r', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    for row in reader:
        district = " ".join(row['District'].split())
        car_no = row['Car Side Number'].strip()
        license_no = row['License Number'].strip()
        emp_name = row['Employee Name'].strip()
        op_day = ", ".join([d.strip() for d in row['Operation Day'].split() if d.strip()])
        
        # เก็บข้อมูลรถโดยใช้ License Number เป็น Unique ID
        if license_no and license_no not in vehicles_map:
            vehicles_map[license_no] = {
                "vehicle_id": license_no,
                "car_side_number": car_no,
                "district": district
            }
            
        # เก็บข้อมูลพนักงาน
        if emp_name and emp_name not in workers_map:
            workers_map[emp_name] = {
                "worker_id": f"W_{worker_counter:04d}",
                "name": emp_name,
                "district": district,
                "raw_op_day": op_day
            }
            worker_counter += 1

route_master = defaultdict(lambda: {'workers': set(), 'license': None, 'op_days': set()})
with open("bma_waste_routes_v2.csv", mode='r', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    for row in reader:
        car_no = row['Car Side Number'].strip()
        license_no = row['License Number'].strip()
        emp_name = row['Employee Name'].strip()
        
        # เก็บข้อมูลพนักงาน (ใช้ Set เพื่อกันชื่อซ้ำ)
        # เราจะไปหา ID ของพนักงานทีหลังหลังจากรู้ว่าคนไหนชื่ออะไร
        if emp_name:
            route_master[car_no]['workers'].add(emp_name)
        
        route_master[car_no]['license'] = license_no
        route_master[car_no]['op_days'].add(row['Operation Day'].strip())

# = สร้าง Map ชื่อพนักงาน -> ID เพื่อใช้ดึงข้อมูล
name_to_id = {name: f"W_{i+1:04d}" for i, name in enumerate(sorted(set(
    name for data in route_master.values() for name in data['workers']
)))}

# =========================================================
# STEP 2: โหลดข้อมูลพิกัด (JSON) เพื่อสร้าง Node และ Edge
# =========================================================
with open("credential.json", 'r', encoding='utf-8') as f:
    raw_data = json.load(f)
    if isinstance(raw_data, list) and len(raw_data) > 0 and isinstance(raw_data[0], list):
        raw_data = raw_data[0]

nodes_map = {}
edges_map = {}
json_route_paths = {} # เก็บ map ระหว่าง car_side_number -> (route_id, edge_sequence)
node_counter = 1
edge_counter = 1

for route in raw_data:
    route_id = route.get('id')
    coords_str = route.get('coordinates') or '[]'
    
    # แกะ Car Side Number ออกมาเพื่อใช้เป็นตัวเชื่อมไปผูกกับ CSV
    detail_html = route.get('detail', '')
    car_match = re.search(r'Car Side Number(?:<\/span>)?(?:&nbsp;|\s)*([0-9\-\s]+)', detail_html)
    car_no = car_match.group(1).strip() if car_match else ""

    coords = json.loads(coords_str)
    path_nodes = []
    sequence_of_edges = []
    
    if len(coords) >= 2:
        path_nodes.append(coords[0])
        for i in range(1, len(coords) - 1):
            if get_angle(coords[i-1], coords[i], coords[i+1]) >= 45:
                path_nodes.append(coords[i])
        path_nodes.append(coords[-1])
        
        for i in range(len(path_nodes) - 1):
            p1, p2 = path_nodes[i], path_nodes[i+1]
            k1 = (round(p1['lat'], 4), round(p1['lng'], 4))
            k2 = (round(p2['lat'], 4), round(p2['lng'], 4))
            
            if k1 not in nodes_map:
                nodes_map[k1] = f"N_{node_counter:05d}"
                node_counter += 1
            if k2 not in nodes_map:
                nodes_map[k2] = f"N_{node_counter:05d}"
                node_counter += 1
                
            u, v = nodes_map[k1], nodes_map[k2]
            
            edge_key = (u, v)
            if edge_key not in edges_map:
                edges_map[edge_key] = {
                    "edge_id": f"E_{edge_counter:05d}",
                    "source": u,
                    "target": v,
                    "distance_m": calc_distance(p1, p2)
                }
                edge_counter += 1
                
            sequence_of_edges.append(edges_map[edge_key]["edge_id"])
            
    # บันทึกข้อมูลแผนที่เส้นทางเก็บไว้รอมารวมร่าง
    if car_no:
        json_route_paths[car_no] = {
            "route_id": route_id,
            "edge_sequence": sequence_of_edges
        }

# =========================================================
# STEP 3: วนลูป CSV อีกรอบเพื่อแมปหา Worker_ID และรวมร่างสร้าง Route Data
# =========================================================
final_routes = []

with open("bma_waste_routes_v2.csv", mode='r', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    for row in reader:
        car_no = row['Car Side Number'].strip()
        license_no = row['License Number'].strip()
        emp_name = row['Employee Name'].strip()
        op_day = ", ".join([d.strip() for d in row['Operation Day'].split() if d.strip()])
        
        # 1. ค้นหา Worker ID จากชื่อใน Master Data
        worker_id = workers_map[emp_name]["worker_id"] if emp_name in workers_map else None
        
        # 2. ค้นหา Route ID และ Edge Sequence จากข้อมูล JSON ที่ทำดัชนีไว้ด้วย Car Side Number
        route_info = json_route_paths.get(car_no, {})
        route_id = route_info.get("route_id", f"R_UNKNOWN_{car_no}")
        edge_sequence = route_info.get("edge_sequence", [])
        
        final_routes.append({
            "route_id": route_id,
            "vehicle_id": license_no if license_no else None,
            "worker_id": worker_id, # ดึงจาก Master จิ้มตรงๆ ตามชื่อพนักงานใน CSV มั่นใจได้ว่าไม่หลุด null แน่นอน
            "operation_day": op_day,
            "edge_sequence": edge_sequence
        })

final_routes = []
for car_no, info in route_master.items():
    # ดึงเส้นทางจาก JSON (ถ้ามี)
    route_info = json_route_paths.get(car_no, {"route_id": f"R_{car_no}", "edge_sequence": []})
    
    # แปลงชื่อพนักงานเป็น ID List
    worker_ids = [name_to_id[name] for name in info['workers']]
    
    final_routes.append({
        "route_id": route_info['route_id'],
        "vehicle_id": info['license'],
        "worker_ids": worker_ids, # เป็น List แล้ว
        "operation_day": list(info['op_days']),
        "edge_sequence": route_info['edge_sequence']
    })

# =========================================================
# STEP 4: บันทึกข้อมูลและส่งออกไฟล์ JSON
# =========================================================
final_nodes = [{"node_id": v, "lat": k[0], "lng": k[1]} for k, v in nodes_map.items()]
final_edges = list(edges_map.values())
final_vehicles = list(vehicles_map.values())
final_workers = [{"worker_id": w["worker_id"], "name": name, "district": w["district"]} for name, w in workers_map.items()]

with open("data/1_nodes.json", "w", encoding="utf-8") as f:
    json.dump(final_nodes, f, ensure_ascii=False, indent=2)
with open("data/1_edges.json", "w", encoding="utf-8") as f:
    json.dump(final_edges, f, ensure_ascii=False, indent=2)
with open("data/2_vehicles.json", "w", encoding="utf-8") as f:
    json.dump(final_vehicles, f, ensure_ascii=False, indent=2)
with open("data/2_workers.json", "w", encoding="utf-8") as f:
    json.dump(final_workers, f, ensure_ascii=False, indent=2)
with open("data/3_transferred_routes.json", "w", encoding="utf-8") as f:
    json.dump(final_routes, f, ensure_ascii=False, indent=2)

print("========================================")
print("      PROGRAM STATE AFTER EXECUTION     ")
print("========================================")
print(f"Total Nodes Processed       : {len(final_nodes)}")
print(f"Total Edges Processed       : {len(final_edges)}")
print(f"Total Vehicles Extracted    : {len(final_vehicles)}")
print(f"Total Workers Extracted     : {len(final_workers)}")
print(f"Total Transferred Routes    : {len(final_routes)}")
print("========================================")
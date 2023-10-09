# Importing dependencies for outside the QGIS Python console
from qgis.PyQt.QtCore import (
    QRectF,)
from qgis.core import (
    QgsProject,
    QgsLayerTreeModel,QgsVectorLayer,QgsApplication,
  QgsDataSourceUri,
  QgsDistanceArea, #for area calculation
  QgsCategorizedSymbolRenderer,
  QgsClassificationRange,
  QgsPointXY,
  QgsProject,
  QgsExpression,
  QgsField,
  QgsFields,
  QgsFeature,
  QgsFeatureRequest,
  QgsFeatureRenderer,
  QgsGeometry,
  QgsGraduatedSymbolRenderer,
  QgsMarkerSymbol,
  QgsMessageLog,
  QgsRectangle,
  QgsRendererCategory,
  QgsRendererRange,
  QgsSymbol,
  QgsVectorDataProvider,
  QgsVectorLayer,
  QgsVectorFileWriter,
  QgsWkbTypes,
  QgsSpatialIndex,
  QgsVectorLayerUtils)
from qgis.gui import (
    QgsLayerTreeView,)
from qgis.core.additions.edit import edit
from qgis.PyQt.QtGui import (QColor,)
from qgis.PyQt.QtCore import QVariant

import datetime



################### What the script does#########################################

# 1. Load in layer by name
# 2. Iterate over the features of the layer (i.e. the rows in the attribute table)
#     2.1 Delete the feature if it is below 10m2 or has an invalid geometry
# 3. For each feature, one by one, create a bounding box of the feature that is iterated over and select all other features from the layer that intersect it
# 4. Iterate once over the selection and add the feature to the result layer, if it is disjoint.
# 5. Iterating over the selection (to only check the selection for each geometry compared to checking all features of the layer)
#   If a selected feature xxx the iterated over feature...
#
#   5.1 touches, it is transferred to the result layer
#   5.2 equals, the iterated over feature gets the ID from the selected feature, IF it didn't have an ID before; the selected feature is deleted; equals are accounted for with Wkt (all nodes in a polygon as point coords)
#   5.3 overlaps, it is deleted
# 6. Statistics for the input and result layer are calculated and displayed



#Initiating variables
name = 'Transektpolygoner_from_raw_data' 
transect_id = 'Comment'

layer = QgsProject.instance().mapLayersByName( name )[0]
features = layer.getFeatures()
index = QgsSpatialIndex(layer.getFeatures()) # Spatial index improves algorithm performance a lot by using bounding boxes
caps = layer.dataProvider().capabilities() #checking the layers possible functions
feature_count = layer.featureCount() #number of features
d= QgsDistanceArea() #initiate area calculation instance
d.setSourceCrs(layer.crs(), QgsProject.instance().transformContext()) #make the calculation use thelayers CRS
d.setEllipsoid(QgsProject.instance().ellipsoid()) #make the calculation use the projects ellipsoid
layer.startEditing() #needed for code to have effect


# collecting invalid or very small geometries
invalid_geoms = 0
let_10m2_geoms = 0


#initiating the memory result layer and fetching its fields
result_layer = QgsVectorLayer('Polygon?crs=EPSG:25833', 'Transect tool result','memory') #defining a memory layer in UTM33N
pr = result_layer.dataProvider() #necessary to add fields and populate them
pr.addAttributes(layer.fields()) #fetch fields/columns from original layer
result_layer.updateFields() #refresh fields from none to what we just defined
result_layer.startEditing() #trigger edit mode to be able to make changes


#Overlay type counters 

touches = []
equals = []
overlaps = []
disjoint = []
row_id = 'row_num'
transferred_geometries = set() #stores WKT geoms
deleted_features = [] #which features were deleted?
weird_sizes = set() #weirdly big or small sizes [[id,area],[id,area]]
result_layer_ids = [] #list to account for possible doubles
result_layer_null_IDs = 0 #counter of transferred NULL IDs
null_count_original = 0
null_count_result = 0
initial_feature_count = layer.featureCount()


#  function that copies features over from the original layer to a memory/temporary layer to work non-destructively on the source layer
def transfer(original_layer,result,variable_name):
    new_feature = QgsFeature(original_layer.fields()) #use the orignial layers fields
    new_feature.setGeometry(variable_name.geometry()) #use the geometry too
    new_feature.setAttributes(variable_name.attributes()) # use the attributes in the fields too
    global result_layer_null_IDs #fetch the global variable 
    global transferred_geometries #used for equaling features
    global result_layer_ids
    transferred_geometries.add(variable_name.geometry().asWkt()) # adding polygon coordinates to the set transferred_geometries; sets can't have double entries
    if new_feature[row_id] not in result_layer_ids: #if the feature with that ID is not yet in the layer, add it
        result_layer_ids.append(new_feature[row_id]) 
        if not new_feature[transect_id]:
            result_layer_null_IDs += 1
            result.addFeature(new_feature)
        else:
            result.addFeature(new_feature)


#function to check for double IDs in the layer at the end
def doubles_check(layer):
    fts =[]
    for i in result_layer.getFeatures():
        if i[row_id] not in fts:
            fts.append(i[row_id])
    print(f"\nSanity check: {result_layer.featureCount()/len(fts)} --> 1.0 means that there are no features added twice from the input layer.")
    

# adding a new field for row numbers to be able to account for NULL IDs (since they can't be accounted for by ID)
#deleting a theoretical old row_id
if row_id in layer.fields().names():
    idx = layer.fields().indexFromName(row_id)
    layer.dataProvider().deleteAttributes([idx])
    layer.updateFields()
layer.dataProvider().addAttributes([QgsField(row_id,QVariant.Int)])
layer.updateFields()


#adding a new row_id
row = 1
for feature in layer.getFeatures():
    feature[row_id] = row
    layer.updateFeature(feature)
    row += 1


#run below line of code to delete a column by name from the input layer
#layer.dataProvider().deleteAttributes([layer.fields().indexFromName('overlay_type')])


starting = datetime.datetime.now().strftime("%H:%M:%S")
print("###################################################################################")
print(f"Transect-polygon-block tool started at {starting}.\n")


# Starting the overarching for loop that iterates over each feature
#looping over the input layer
for feature in features: #for each feature in the layer
    geometries = feature.geometry() #get the features geometry
    overlay_type = '' #for the overlay type field in the resulting layer


#count the number of NULL values in the original layer
    if not feature[transect_id]: 
        null_count_original += 1


# deleting features that do not have a valid geometry or are below 10m2
    if feature.geometry().isGeosValid():
        if QgsDistanceArea().measureArea(feature.geometry()) < 10: #if block is less then 10m2
            if feature[row_id] not in deleted_features:
                deleted_features.append(feature[row_id])
            layer.deleteFeature(feature.id())
            let_10m2_geoms += 1
            continue
        elif not feature.geometry().isGeosValid():
            if i[row_id] not in deleted_features:
                deleted_features.append(i[row_id])
            layer.deleteFeature(feature.id())
            invalid_geoms += 1
            continue
            
    
# Find the intersecting feature IDs using the spatial index; uses bounding box; makes code fast
    intersecting_feature_ids = index.intersects(feature.geometry().boundingBox()) #get IDs as a list that intersect the features bounding box to perform the analysis on these instead of all the layers features
    intersecting_feature_ids.remove(feature.id()) #removing the feature ID that we iterate over, quick fix


# Select the intersecting features by their IDs
    layer.selectByIds(intersecting_feature_ids) #selecting the intersecting features for the feature iterated over right now
    selection = layer.selectedFeatures() #creating a variable of the selection that can be iterated over
    

#working with the selection
    intersections_ids = []
    intersections_areas = []
        

#fetch IDs for NULL IDs from equaling or overlapping features
    for i in selection:
        if i.geometry().equals(feature.geometry()): #get ID from equaling polygons first
            if not feature[transect_id] and i[transect_id]:
                feature[transect_id]=i[transect_id]
                layer.updateFields() #update the change made above
        elif i.geometry().overlaps(feature.geometry()): #get ID from overlapping polygon when equaling failed
            if not feature[transect_id] and i[transect_id]:
                feature[transect_id]=i[transect_id]
                layer.updateFields() #update the change made above


# Checking disjoint before all other; Assume the feature is disjoint from all until proven otherwise
    disjoint_from_all = True 
    for i in selection:
        if i[row_id] == feature[row_id]: # Skip the feature itself
            continue
        if not i.geometry().disjoint(feature.geometry()): #if only one feature is not disjoint, "flip the swtich"
            disjoint_from_all = False
    # After checking against all other features, if it's still disjoint from all:
    if disjoint_from_all or len(selection) == 0:# if the feature is disjoint or the feature count intersecting with its bounding box is 0 (another way to express a disjoint here)
        transfer(layer, pr, feature)
        if i[row_id] not in disjoint:
            disjoint.append(i[row_id])


# Overlay analysis
    for i in selection:
        area_block = d.measureArea(i.geometry())
        if i[row_id] in result_layer_ids:
            continue

        if i.geometry().equals(feature.geometry()):
            #checks if the WKT-geometry already was transferred, works with coordinates instead of ids
            if i.geometry().asWkt() in transferred_geometries:
                continue
            else:
                if 'equals' not in overlay_type:
                    overlay_type += 'equals' #append overlay type
                if i[row_id] not in deleted_features:
                    deleted_features.append(i[row_id])
                if i[row_id] not in equals:
                    equals.append(i[row_id])
                transfer(layer,pr,i)


        if i.geometry().touches(feature.geometry()): #returns 339 blocks
        #if QgsExpression("\"overlay_result\" LIKE '%touches%'"): #returns 874 blocks; uses the QGIS field calculator expression in the pre-calculated field, if it at least touches another polygon
            if 'touches' not in overlay_type:
                overlay_type += 'touches' #append overlay type
            if i[row_id] not in result_layer_ids:
                transfer(layer,pr,i) #copy features over to memory layer
            if i[row_id] not in touches: #update counter
                touches.append(i[row_id])
                
        
        if i.geometry().overlaps(feature.geometry()): #touching is not enough, polygons have to have a shared area
            if 'overlaps' not in overlay_type:
                overlay_type += 'overlaps' #append overlay type
            if i[row_id] not in overlaps:
                overlaps.append(i[row_id])
            if i[row_id] not in deleted_features:
                deleted_features.append(i[row_id])
        
            
            #collecting weird sizes if the feature still exists
            if area_block > 120 or area_block <80 and area_block:
                weird_sizes.add(i[row_id])
    
# getting max distance to iterated over feature (not used in the end)
#    if len(intersections_ids)>1: #if there are any intersections (keeps the max function from throwing an error)
#        target_number = 100
#        furthest_index = max(range(len(intersections_areas)), key=lambda i: abs(intersections_areas[i] - target_number))
#        #print(f" The furthest apart area from {target_number} is {intersections_areas[furthest_index]} at the index {furthest_index}(of {len(intersections_ids)} total)")

#Final operations after the code ran

print(f"{result_layer.featureCount()}/{initial_feature_count} ({str(result_layer.featureCount()/layer.featureCount())[0:4]}%) blocks were added, thereof {result_layer_null_IDs} NULL IDs ({str((result_layer_null_IDs/result_layer.featureCount())*100)[0:4]}%)")
print(f"In the source layer the following amount of overlaps was detected:\n    -> Equals: {len(equals)} Overlaps: {len(overlaps)} Touches: {len(touches)} Disjoint: {len(disjoint)} (one feature can interact with more than one feature at a time)")
print(f"{layer.featureCount() - result_layer.featureCount()} features were deleted from the layer.")
print(f"{len(weird_sizes)} unusual sized blocks detected but not deleted (80m2 < x > 120m2).")
print(f"{let_10m2_geoms} geometries under 10m2 and {invalid_geoms} invalid geometries were deleted from the input layer.")

#layer.dataProvider().deleteAttributes([layer.fields().indexFromName('row_num_1')])
layer.rollBack() #do not save any changes to the original layer
layer.selectByIds([]) #deselct all features to avoid confusion later on
result_layer.commitChanges() #saving the layers changes to new memory layer


#How many NULL value-IDs are in the result layer?
for i in result_layer.getFeatures():
    if not i[transect_id]:
        null_count_result += 1

print(f"The source layer had {null_count_original}/{layer.featureCount()} ({str((null_count_original/layer.featureCount())*100)[0:4]}%) NULL values for transect ID, the result has {result_layer_null_IDs}/{result_layer.featureCount()} ({str((result_layer_null_IDs/result_layer.featureCount())*100)[0:4]}%).")
doubles_check(result_layer) #sanity check for double IDs; if Sanity check = 1.0 then there are no doubles
QgsProject.instance().addMapLayer(result_layer)


############## TO DO ##############################
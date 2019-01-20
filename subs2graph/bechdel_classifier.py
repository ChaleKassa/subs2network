from turicreate import SFrame
from subs2graph.consts import DATA_PATH, TEMP_PATH
from turicreate import aggregate as agg
import pandas as pd
import math
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier

from sklearn import metrics
from sklearn.metrics import roc_auc_score

from subs2graph.imdb_dataset import imdb_data


def split_vals(a, n): return a[:n].copy(), a[n:].copy()


def rmse(x, y): return math.sqrt(((x - y) ** 2).mean())


def print_score(m, X_train, y_train, X_valid, y_valid):
    res = [rmse(m.predict(X_train), y_train), rmse(m.predict(X_valid), y_valid),
           m.score(X_train, y_train), m.score(X_valid, y_valid)]
    if hasattr(m, 'oob_score_'):
        res.append(m.oob_score_)
    print(res)


def calculate_gender_centrality():
    gender_centrality = pd.read_csv(f"{TEMP_PATH}/gender.csv", index_col=0)

    gender_centrality["rank_pagerank"] = gender_centrality.groupby("movie_name")["pagerank"].rank(
        ascending=False).astype(int)
    rank_pagerank = pd.pivot_table(gender_centrality[["gender", "rank_pagerank"]], index="gender",
                                   columns="rank_pagerank", aggfunc=len).T
    rank_pagerank["F%"] = rank_pagerank["F"] / (rank_pagerank["F"] + rank_pagerank["M"])
    rank_pagerank["M%"] = rank_pagerank["M"] / (rank_pagerank["F"] + rank_pagerank["M"])
    for gender in set().union(gender_centrality.gender.values):
        gender_centrality[gender] = gender_centrality.apply(lambda _: int(gender in _.gender), axis=1)
    gender_centrality = gender_centrality.sort_values(["movie_name", "rank_pagerank"])
    return gender_centrality


def get_female_in_top_10_roles():
    gender_centrality = calculate_gender_centrality()
    gender_centrality_movies = gender_centrality[gender_centrality["rank_pagerank"] < 11].groupby("movie_name").agg(
        ["sum", "count"])
    female_in_top_10 = pd.DataFrame()
    female_in_top_10["F_top10"] = gender_centrality_movies["F"]["sum"] / gender_centrality_movies["F"]["count"]
    female_in_top_10["year"] = gender_centrality_movies["year"]["sum"] / gender_centrality_movies["year"]["count"]
    female_in_top_10["movie_name"] = gender_centrality_movies.index.str.replace(" - roles", "")
    female_in_top_10["year"] = female_in_top_10["year"].astype(int)
    return female_in_top_10


def anlayze_triangles():
    triangles = SFrame.read_csv(f"{TEMP_PATH}/triangles.csv", usecols=["0", "1", "2", "3", "4"])
    triangles_gender = triangles.apply(
        lambda x: [imdb_data.get_actor_gender(x["0"]), imdb_data.get_actor_gender(x["1"]),
                   imdb_data.get_actor_gender(x["2"])])
    triangles_gender = triangles_gender.unpack()
    triangles_gender["movie"] = triangles["3"]
    triangles_gender["year"] = triangles["4"]
    triangles_gender = triangles_gender.dropna()
    triangles_gender = triangles_gender.join(imdb_data.title, {"movie": "primaryTitle", "year": "startYear"})
    return triangles_gender


class BechdelClassifier(object):

    def __init__(self):
        self.bechdel = SFrame.read_csv(f"{DATA_PATH}/bechdel.csv", column_type_hints={"imdbid": str})
        self.bechdel.sort("year", False)
        self.bechdel["tconst"] = "tt" + self.bechdel["imdbid"]
        self.bechdel_imdb = imdb_data.title.join(self.bechdel)
        self.clf = RandomForestClassifier(n_jobs=-1, n_estimators=100, max_depth=5, random_state=1)

    def build_dataset(self):
        try:
            self.graph_features = SFrame.read_csv(f"{DATA_PATH}/bechdel_features.csv")
        except:
            t = self.triangles()
            graph_features = SFrame.read_csv("../temp/graph_features.csv")

            graph_features = graph_features.join(SFrame(get_female_in_top_10_roles()),
                                                 on={"movie_name": "movie_name", "year": "year"})
            self.graph_features = graph_features.join(SFrame(t), on={"movie_name": "movie", "year": "year"})
            self.graph_features["total_tir"] = self.graph_features["0"] + self.graph_features["1"] + \
                                               self.graph_features["2"] + self.graph_features["3"]
            for i in range(4):
                self.graph_features[f"{i}%"] = self.graph_features[str(i)] / self.graph_features["total_tir"]

            self.graph_features.save(f"{DATA_PATH}/bechdel_features.csv", "csv")
        self.graph_features = imdb_data.title.filter_by("movie", "titleType").join(self.graph_features,
                                                                                   on={"primaryTitle": "movie_name",
                                                                                       "startYear": "year"})
        self.graph_features = self.graph_features[self.graph_features["node_number"] > 5]
        bechdel_ml = self.graph_features.join(self.bechdel_imdb,
                                              on={"primaryTitle": "primaryTitle", "startYear": "year"}, how='left')
        # bechdel_triangles = SFrame(traingles_at_movie).join(self.bechdel_imdb, {"tconst": "tconst"})

        bechdel_ml = bechdel_ml[bechdel_ml["genres"]!=None]
        bechdel_ml = bechdel_ml.to_dataframe()
        bechdel_ml["genres"] = bechdel_ml.genres.str.split(",")
        for genre in set().union(*bechdel_ml.genres.values):
            bechdel_ml[genre] = bechdel_ml.apply(lambda _: int(genre in _.genres), axis=1)

        train = bechdel_ml[bechdel_ml["rating"].notnull()]
        val = bechdel_ml[bechdel_ml["rating"].isnull()]
        val = val.fillna(0)
        train = train.fillna(0)
        train["rating"] = train["rating"] == 3

        self.val_title = val.pop('title')
        self.title = train.pop('title')
        self.X_train = train.drop(
            ["X1", "genres", "imdbid", "originalTitle", 'endYear', 'isAdult', 'tconst',
             'titleType', 'tconst.1', 'titleType.1', 'originalTitle.1', 'isAdult.1', 'startYear.1', 'endYear.1',
             'runtimeMinutes.1', 'genres.1','primaryTitle',
             'X1',
             'id',
             'imdbid',
             'id'], axis=1)
        self.val = val.drop(
            ["X1", "genres", "imdbid", "originalTitle", 'endYear', 'isAdult', 'tconst',
             'titleType', 'tconst.1', 'titleType.1', 'originalTitle.1', 'isAdult.1', 'startYear.1', 'endYear.1',
             'runtimeMinutes.1', 'genres.1','primaryTitle',
             'X1',
             'id',
             'imdbid','rating',
             'id'], axis=1)
        self.y = self.X_train.pop("rating")
        # bechdel_imdb_rating[(bechdel_imdb_rating["numVotes"] > 5000) & (bechdel_imdb_rating["titleType"] == "movie")][1000:]

    def triangles(self):
        triagles_gender = anlayze_triangles()
        triagles_gender["1"] = triagles_gender["X.0"] == "M"
        triagles_gender["2"] = triagles_gender["X.1"] == "M"
        triagles_gender["3"] = triagles_gender["X.2"] == "M"
        triagles_gender["total"] = triagles_gender["1"] + triagles_gender["2"] + triagles_gender["3"]

        moive_triangle = triagles_gender.groupby(["movie", "year", "total"], operations={'count': agg.COUNT()})
        # type(moive_triangle)
        traingles_at_movie = moive_triangle.to_dataframe().pivot_table(index=["movie", "year"], values="count",
                                                                       columns='total',
                                                                       aggfunc=lambda x: x)
        traingles_at_movie = traingles_at_movie.fillna(0)

        traingles_at_movie = traingles_at_movie.reset_index()
        # bechdel_triangles = SFrame(traingles_at_movie).join(self.bechdel_imdb, {"tconst": "tconst"})
        return traingles_at_movie

    def train_test(self):
        # self.y = self.bechdel_ml.pop("rating")
        n_valid = 1000
        X_valid, X_train = split_vals(self.X_train, n_valid)
        y_valid, y_train = split_vals(self.y, n_valid)

        self.clf.fit(X_train, y_train)
        # print_score(self.clf, X_train, y_train, X_valid, y_valid)
        return roc_auc_score(y_valid, self.clf.predict_proba(X_valid)[:, 1])

    def train(self):
        self.clf = RandomForestClassifier(n_jobs=-1, n_estimators=100, max_depth=5, random_state=1)
        self.clf.fit(self.X_train, self.y)
        return self.clf


b = BechdelClassifier()
b.build_dataset()
# print(b.train_test())
rfc = b.train()
v = rfc.predict_proba(b.val)[:, 1]
print(v.mean())
for y, d in b.val.groupby("startYear"):
    if len(d) > 10:
        v = rfc.predict_proba(d)[:, 1]
        print(y, v.mean())